class SmartCrop:
    """Crop an image around a subject bounding box.

    The box (fractions of the image size) is padded, grown - never shrunk - to
    `aspect` (width / height, or None to keep the image's own aspect ratio),
    then nudged back inside the image.
    """

    def __init__(self, bbox, aspect=None, padding=0.1):
        self.bbox = bbox
        self.aspect = aspect
        self.padding = padding

    def process(self, image):
        image_width, image_height = image.size
        x1, y1, x2, y2 = (
            self.bbox[0] * image_width,
            self.bbox[1] * image_height,
            self.bbox[2] * image_width,
            self.bbox[3] * image_height,
        )

        padding_x = (x2 - x1) * self.padding
        padding_y = (y2 - y1) * self.padding
        x1, y1 = x1 - padding_x, y1 - padding_y
        x2, y2 = x2 + padding_x, y2 + padding_y

        width, height = x2 - x1, y2 - y1
        aspect = self.aspect or image_width / image_height

        if width / height < aspect:
            width = height * aspect
        else:
            height = width / aspect

        if width > image_width:
            width, height = image_width, image_width / aspect
        if height > image_height:
            width, height = image_height * aspect, image_height

        centre_x, centre_y = (x1 + x2) / 2, (y1 + y2) / 2
        left = min(max(centre_x - width / 2, 0), image_width - width)
        top = min(max(centre_y - height / 2, 0), image_height - height)

        return image.crop(
            (round(left), round(top), round(left + width), round(top + height))
        )
