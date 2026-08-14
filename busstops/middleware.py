import re
from http import HTTPStatus

from django.http import HttpResponse
from django.utils.cache import add_never_cache_headers
from django_ratelimit import ALL
from django_ratelimit.core import is_ratelimited

from whitenoise.middleware import WhiteNoiseMiddleware


class HealthCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/up":
            # bypass ALLOWED_HOSTS check
            response = HttpResponse("up!")
        else:
            response = self.get_response(request)

        return response


class WhiteNoiseWithFallbackMiddleware(WhiteNoiseMiddleware):
    def immutable_file_test(self, path, url):
        # ensure that cache-control headers are added
        # for files with hashes added by parcel e.g. "dist/js/BigMap.19ec75b5.js"
        if re.match(r"^.+\.[0-9a-f]{8,12}\..+$", url):
            return True
        return super().immutable_file_test(path, url)

    # https://github.com/evansd/whitenoise/issues/245
    def __call__(self, request):
        response = super().__call__(request)
        if response.status_code == HTTPStatus.NOT_FOUND and request.path.startswith(
            self.static_prefix
        ):
            add_never_cache_headers(response)
        return response


def pin_db_middleware(get_response):
    from multidb.pinning import pin_this_thread, unpin_this_thread

    def middleware(request):
        if (
            request.method == "POST"
            or request.path.startswith("/admin/")
            or request.path.startswith("/accounts/")
            or "/edit" in request.path
        ):
            pin_this_thread()
        else:
            unpin_this_thread()
        return get_response(request)

    return middleware


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self.should_rate_limit(request):
            return HttpResponse(
                "Rate limit exceeded. Please slow down.",
                status=429,
                headers={"Retry-After": "60"},
            )
        return self.get_response(request)

    def should_rate_limit(self, request):
        if request.path.startswith(("/static/", "/media/", "/up", "/version")):
            return False
        if request.path in ("/robots.txt", "/sitemap.xml", "/api"):
            return False
        if request.path.startswith("/api/"):
            return False
        if request.path.endswith((".css", ".js", ".png", ".jpg", ".gif", ".ico", ".svg")):
            return False

        rate = "1000/m"
        return is_ratelimited(
            request,
            group="general",
            key="ip",
            rate=rate,
            method=ALL,
            increment=True,
        )
