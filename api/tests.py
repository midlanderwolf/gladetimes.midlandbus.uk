from django.test import TestCase


class ApiTest(TestCase):
    def test_api(self):
        with self.assertNumQueries(1):
            response = self.client.get(
                "/api/vehicles/",
            )

        # extra queries from livery, operator and type filter widgets
        with self.assertNumQueries(1):
            response = self.client.get(
                "/api/vehicles/", headers={"accept": "text/html"}
            )

        self.assertContains(response, "<title>Vehicle List – API – gladetimes</title>")
        self.assertContains(response, "<a class='navbar-brand' href='/'>gladetimes</a>")
