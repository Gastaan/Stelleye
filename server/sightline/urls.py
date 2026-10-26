from django.conf import settings
from django.contrib import admin
from django.contrib.staticfiles.views import serve
from django.urls import include, path, re_path
from django.views.decorators.cache import cache_control

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("people.urls")),
    path("", include("calls.urls")),
    path("review/", include("disputes.urls")),
    path("wallet/", include("wallets.urls")),
]

if settings.DEBUG:
    # Run with `runserver --nostatic` so static files come from here: browsers re-check them on every
    # load (a cheap 304 when unchanged), so a page never pairs fresh HTML with a stale cached script.
    urlpatterns += [re_path(r"^static/(?P<path>.*)$", cache_control(no_cache=True)(serve))]
