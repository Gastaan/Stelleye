from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("people.urls")),
    path("", include("calls.urls")),
    path("review/", include("disputes.urls")),
    path("wallet/", include("wallets.urls")),
]
