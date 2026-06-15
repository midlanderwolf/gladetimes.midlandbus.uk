from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.views import shortcut
from django.http import Http404
from django.urls import include, path
from django.contrib import admin
from api import api


def admin_shortcut(request, content_type_id, object_id):
    try:
        return shortcut(request, content_type_id, object_id)
    except ContentType.MultipleObjectsReturned:
        raise Http404


urlpatterns = [
    path(
        "admin/r/<int:content_type_id>/<path:object_id>/",
        admin.site.admin_view(admin_shortcut),
        name="admin_shortcut",
    ),
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("api/", include(api.router.urls)),
    path("", include("busstops.urls")),
]


handler404 = "busstops.views.not_found"
