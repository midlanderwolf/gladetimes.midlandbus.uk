from django.core.management.base import BaseCommand
from django.db.models import Q
from PIL import Image
from tqdm import tqdm

from ...exif import get_exif
from ...models import Photo


class Command(BaseCommand):
    """Populate width/height/metadata/location/taken_at for photos that
    don't have them yet
    """

    def handle(self, *args, **options):
        photos = Photo.objects.filter(Q(width=None) | Q(metadata={})).order_by("id")

        updated = 0
        for photo in tqdm(photos.iterator(), total=photos.count()):
            try:
                with photo.image.open() as f:
                    image = Image.open(f)
                    photo.width, photo.height = image.size
                    metadata, location, taken_at = get_exif(image)
            except OSError as e:
                # missing from storage, or not a readable image
                self.stderr.write(f"{photo.id} {e}")
                continue

            if metadata:
                photo.metadata["exif"] = metadata
            if location and not photo.location:
                photo.location = location
            if taken_at and not photo.taken_at:
                photo.taken_at = taken_at

            photo.save(
                update_fields=["width", "height", "metadata", "location", "taken_at"]
            )
            updated += 1

        self.stdout.write(f"updated {updated} photos")
