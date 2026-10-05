from django.core.management.base import BaseCommand
from tqdm import tqdm

from ...detect import update_photo
from ...models import Photo


class Command(BaseCommand):
    """Detect the vehicle in photos that don't have a bounding box yet
    (and download the model, if necessary)
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="also redetect photos that already have a bounding box",
        )

    def handle(self, *args, all: bool, **options):
        photos = Photo.objects.order_by("id")
        if not all:
            photos = photos.filter(bbox=None)

        updated = 0
        for photo in tqdm(photos.iterator(), total=photos.count()):
            try:
                updated += update_photo(photo, force=all)
            except OSError as e:
                # missing from storage, or not a readable image
                self.stderr.write(f"{photo.id} {e}")

        self.stdout.write(f"updated {updated} photos")
