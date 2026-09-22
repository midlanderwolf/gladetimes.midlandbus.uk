from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings


class ListenTest(TestCase):
    @override_settings(NEW_VEHICLE_WEBHOOK_URL=None)
    def test_missing_setting(self):
        with self.assertRaises(AssertionError):
            call_command("listen")

    @override_settings(NEW_VEHICLE_WEBHOOK_URL="http://example.com")
    def test_handle(self):
        with (
            mock.patch(
                "vehicles.management.commands.listen.connection.cursor"
            ) as mock_cursor,
            mock.patch(
                "vehicles.management.commands.listen.requests.Session.post"
            ) as mock_post,
            mock.patch("vehicles.management.commands.listen.time.sleep"),
        ):
            notifies = (
                mock_cursor.return_value.__enter__.return_value.connection.notifies
            )
            notifies.side_effect = [
                [mock.Mock(payload="sndr-p420-kak")],  # stop_after=1
                [mock.Mock(payload="loth-199")],  # timeout=WINDOW
                KeyboardInterrupt,  # stop listening
            ]
            with self.assertRaises(KeyboardInterrupt):
                call_command("listen")

        # debounced into one message
        mock_post.assert_called_once_with(
            "http://example.com",
            json={
                "username": "bot",
                "content": "[sndr-p420-kak](https://bustimes.org/vehicles/sndr-p420-kak)\n"
                "[loth-199](https://bustimes.org/vehicles/loth-199) <@813528710404898817>",
            },
            timeout=10,
        )
