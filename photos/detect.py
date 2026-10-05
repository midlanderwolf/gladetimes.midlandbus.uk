"""Find the bus in a photo, using a small object detection model (RT-DETR),
so that the photo can be cropped nicely
"""

import functools
import logging

import numpy as np
import requests
from django.conf import settings
from PIL import Image

logger = logging.getLogger(__name__)

MODEL_URL = (
    "https://huggingface.co/onnx-community/rtdetr_r18vd/resolve/main/onnx/model.onnx"
)
SIZE = 640

# COCO class ids
BIG = (5, 6, 7)  # bus, train, truck
CAR = 2

MIN_SCORE = 0.5

# a car only counts as the subject if it fills at least this much of the frame,
# so we don't crop to a parked car in the background
MIN_CAR_AREA = 0.05


def get_model_path():
    return settings.DATA_DIR / "rtdetr_r18vd.onnx"


def download_model():
    path = get_model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("downloading %s", MODEL_URL)
    response = requests.get(MODEL_URL, stream=True, timeout=60)
    response.raise_for_status()
    temp = path.with_suffix(".onnx.tmp")
    with temp.open("wb") as open_file:
        for chunk in response.iter_content(1024 * 1024):
            open_file.write(chunk)
    temp.rename(path)
    return path


@functools.cache
def get_session():
    import onnxruntime

    path = get_model_path()
    if not path.exists():
        download_model()
    return onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])


def detect(image: Image.Image) -> list[list] | None:
    """Return a list of (class id, score, box) tuples,
    where box is [x1, y1, x2, y2] as fractions of the image size
    """
    pixels = np.asarray(
        image.convert("RGB").resize((SIZE, SIZE), Image.BILINEAR), dtype=np.float32
    )
    pixels = pixels.transpose(2, 0, 1)[np.newaxis] / 255

    logits, boxes = get_session().run(None, {"pixel_values": pixels})

    scores = 1 / (1 + np.exp(-logits[0]))  # sigmoid
    classes = scores.argmax(1)
    scores = scores.max(1)

    detections = []
    for i in np.nonzero(scores >= MIN_SCORE)[0]:
        centre_x, centre_y, width, height = boxes[0][i]
        detections.append(
            (
                int(classes[i]),
                float(scores[i]),
                [
                    centre_x - width / 2,
                    centre_y - height / 2,
                    centre_x + width / 2,
                    centre_y + height / 2,
                ],
            )
        )
    return detections


def area(box) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def get_subject(detections) -> list | None:
    """Pick the box most likely to be the vehicle the photo is of -
    the biggest bus, train or truck, or failing that a car big enough
    to plausibly be the subject
    """
    candidates = [box for class_id, _, box in detections if class_id in BIG] or [
        box
        for class_id, _, box in detections
        if class_id == CAR and area(box) >= MIN_CAR_AREA
    ]

    if candidates:
        return [
            # plain floats, so that a box read back from the database hashes
            # the same as a freshly detected one (and clamped to the image,
            # as boxes can stick out slightly)
            float(min(max(value, 0), 1))
            for value in max(candidates, key=area)
        ]


def detect_subject(image_file) -> list | None:
    """Return [x1, y1, x2, y2] as fractions of the image size, or None"""
    closed = image_file.closed
    if closed:
        image_file.open()
    try:
        with Image.open(image_file) as image:
            detections = detect(image)
    finally:
        if closed:
            image_file.close()

    return get_subject(detections)


def update_photo(photo, force=False) -> bool:
    """Detect the subject of a photo and, if it's moved, regenerate the
    cropped versions (the Optimistic cache file strategy only does that
    when the source image itself changes)
    """
    bbox = detect_subject(photo.image)
    if bbox == photo.bbox and not force:
        return False

    photo.bbox = bbox
    photo.save(update_fields=["bbox"])
    for spec in (photo.image_320, photo.image_1200_630):
        spec.generate(force=True)
    return True
