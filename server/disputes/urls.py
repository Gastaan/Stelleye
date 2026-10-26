from django.urls import path

from . import views

urlpatterns = [
    path("", views.review_list, name="review"),
    path("calls/<int:call_id>/report/", views.report, name="call_report"),
    path("<int:report_id>/resolve/", views.resolve, name="resolve"),
    path("guides/<int:profile_id>/verify/", views.verify_guide, name="verify_guide"),
]
