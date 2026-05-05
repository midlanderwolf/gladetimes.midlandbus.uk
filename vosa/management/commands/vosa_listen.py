from django.db import connection
import time
import requests
from django.core.management.base import BaseCommand
from django.conf import settings
from vosa.models import Licence, Registration, Variation


def get_content(payload):
    embed = None
    if payload.startswith("licence:"):
        slug = payload[8:]
        content = f"[{slug}](https://gladetimes.midlandbus.uk/licences/{slug})"
        try:
            licence = Licence.objects.get(licence_number=slug)
            embed = {
                "title": f"{licence.licence_number} - {licence.trading_name or licence.name}",
                "description": licence.address,
                "fields": [
                    {"name": "Type", "value": "New License"},
                    {
                        "name": "Traffic Area",
                        "value": licence.get_traffic_area_display(),
                    },
                    {"name": "Status", "value": licence.licence_status or "Unknown"},
                ],
            }
        except Licence.DoesNotExist:
            pass
    elif payload.startswith("registration:"):
        slug = payload[13:]
        content = f"[{slug}](https://gladetimes.midlandbus.uk/registrations/{slug})"
        try:
            registration = Registration.objects.get(registration_number=slug)
            description = f"{registration.start_point} to {registration.finish_point}"
            if registration.via:
                description += f" via {registration.via}"
            embed = {
                "title": f"{registration.registration_number} - {registration.licence.name}",
                "description": description,
                "fields": [
                    {"name": "Type", "value": "New Registration"},
                    {
                        "name": "Service Number",
                        "value": registration.service_number or "N/A",
                    },
                    {"name": "Status", "value": registration.registration_status},
                ],
            }
        except Registration.DoesNotExist:
            pass
    elif payload.startswith("variation:"):
        parts = payload[9:].split("#")
        slug = parts[0]
        content = f"[{slug}](https://gladetimes.midlandbus.uk/registrations/{slug})"
        try:
            registration = Registration.objects.get(registration_number=slug)
            if len(parts) > 1:
                variation_number = int(parts[1])
                variation = Variation.objects.get(
                    registration=registration, variation_number=variation_number
                )
            else:
                variation = registration.latest_variation
            if variation:
                embed = {
                    "title": f"{registration.registration_number} - {registration.licence.name}",
                    "description": f"{registration.start_point} to {registration.finish_point}",
                    "fields": [
                        {"name": "Type", "value": "Variation"},
                        {
                            "name": "Effective Date",
                            "value": str(variation.effective_date)
                            if variation.effective_date
                            else "N/A",
                        },
                        {
                            "name": "Variation",
                            "value": f"#{variation.variation_number}",
                        },
                        {"name": "Status", "value": variation.registration_status},
                    ],
                }
        except (Registration.DoesNotExist, Variation.DoesNotExist):
            pass
    else:
        content = payload

    return content, embed


class Command(BaseCommand):
    def handle(self, *args, **options):
        assert settings.NEW_LICENSE_WEBHOOK_URL, "NEW_LICENSE_WEBHOOK_URL is not set"

        session = requests.Session()

        with connection.cursor() as cursor:
            cursor.execute("""
CREATE OR REPLACE FUNCTION notify_new_licence()
RETURNS trigger AS $$
BEGIN
PERFORM pg_notify('new_licence', CONCAT('licence:', NEW.licence_number));
RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
            cursor.execute("""
CREATE OR REPLACE TRIGGER notify_new_licence
AFTER INSERT ON vosa_licence
FOR EACH ROW
EXECUTE PROCEDURE notify_new_licence();
""")

            cursor.execute("""
CREATE OR REPLACE FUNCTION notify_new_registration()
RETURNS trigger AS $$
BEGIN
PERFORM pg_notify('new_registration', CONCAT('registration:', NEW.registration_number));
RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
            cursor.execute("""
CREATE OR REPLACE TRIGGER notify_new_registration
AFTER INSERT ON vosa_registration
FOR EACH ROW
EXECUTE PROCEDURE notify_new_registration();
""")

            cursor.execute("""
CREATE OR REPLACE FUNCTION notify_new_variation()
RETURNS trigger AS $$
BEGIN
PERFORM pg_notify('new_variation', CONCAT('variation:', NEW.registration_id, '#', NEW.variation_number));
RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")
            cursor.execute("""
CREATE OR REPLACE TRIGGER notify_new_variation
AFTER INSERT ON vosa_variation
FOR EACH ROW
EXECUTE PROCEDURE notify_new_variation();
""")

            cursor.execute("LISTEN new_licence")
            cursor.execute("LISTEN new_registration")
            cursor.execute("LISTEN new_variation")
            gen = cursor.connection.notifies()
            for notify in gen:
                print(notify)

                content, embed = get_content(notify.payload)

                response = session.post(
                    settings.NEW_LICENSE_WEBHOOK_URL,
                    json={
                        "username": "bustimes VOSA Notifier",
                        "content": content,
                        "embeds": [embed] if embed else [],
                    },
                    timeout=10,
                )

                print(response, response.headers, response.text)

                time.sleep(5)
