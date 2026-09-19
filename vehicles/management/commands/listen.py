import logging
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)

JG = "<@813528710404898817>"
JG_SUBSCRIPTIONS = ("sndr", "obus", "fecs", "kctb", "simo", "lynx")

# Discord message length limit
MAX_LENGTH = 2000

# seconds to wait for more new vehicles, to announce them in one message
WINDOW = 10


def get_link(slug):
    return f"[{slug}](https://bustimes.org/vehicles/{slug})"


def get_chunks(slugs):
    """Split slugs into groups that will each fit in one message

    (a bit overcautious!)"""

    chunk = []
    length = 0

    for slug in slugs:
        line_length = len(get_link(slug)) + 1
        if chunk and length + line_length > MAX_LENGTH - len(JG):
            yield chunk
            chunk, length = [], 0
        chunk.append(slug)
        length += line_length

    if chunk:
        yield chunk


def get_content(slugs):
    content = "\n".join(get_link(slug) for slug in slugs)

    if any(slug[:4] in JG_SUBSCRIPTIONS for slug in slugs):
        content = f"{content} {JG}"

    return content


class Command(BaseCommand):
    def handle(self, *args, **options):
        assert settings.NEW_VEHICLE_WEBHOOK_URL, "NEW_VEHICLE_WEBHOOK_URL is not set"

        session = requests.Session()

        with connection.cursor() as cursor:
            cursor.execute("""CREATE OR REPLACE FUNCTION notify_new_vehicle()
                           RETURNS trigger AS $$
                           BEGIN
                           PERFORM pg_notify('new_vehicle', NEW.slug);
                           RETURN NEW;
                           END;
                           $$ LANGUAGE plpgsql;""")
            cursor.execute("""CREATE OR REPLACE TRIGGER notify_new_vehicle
                           AFTER INSERT ON vehicles_vehicle
                           FOR EACH ROW
                           EXECUTE PROCEDURE notify_new_vehicle();""")

            cursor.execute("LISTEN new_vehicle")
            conn = cursor.connection

            while True:
                # wait for a new vehicle, then for any others that follow shortly after
                payloads = [notify.payload for notify in conn.notifies(stop_after=1)]
                payloads += [notify.payload for notify in conn.notifies(timeout=WINDOW)]

                logger.info(payloads)

                for chunk in get_chunks(payloads):
                    response = session.post(
                        settings.NEW_VEHICLE_WEBHOOK_URL,
                        json={
                            "username": "bot",
                            "content": get_content(chunk),
                        },
                        timeout=10,
                    )

                    logger.info("%s %s %s", response, response.headers, response.text)

                    time.sleep(5)
