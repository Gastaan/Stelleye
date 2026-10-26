from django.urls import path

from . import views

urlpatterns = [
    path("help/", views.member_home, name="member_home"),
    path("guide/", views.guide_home, name="guide_home"),
    path("api/me/", views.me, name="api_me"),
    path("calls/<int:call_id>/confirm/", views.confirm, name="call_confirm"),
    path("calls/<int:call_id>/claim/", views.claim, name="call_claim"),
]
