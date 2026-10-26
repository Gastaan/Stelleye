from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path("llms.txt", views.llms_txt, name="llms_txt"),
    path("login/", views.LoginPage.as_view(), name="login"),
    path("join/", views.signup, name="signup"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("demo-login/<str:username>/", views.demo_login, name="demo_login"),
    path("me/whatsapp/", views.set_whatsapp, name="set_whatsapp"),
    path("guides/<int:guide_id>/save/", views.save_guide, name="save_guide"),
]
