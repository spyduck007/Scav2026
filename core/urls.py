from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("auth/ion/", views.oauth_start, name="oauth_start"),
    path("complete/ion/", views.oauth_callback, name="oauth_callback"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("rules/", views.rules_view, name="rules"),
    path("challenge/", views.challenge_view, name="challenge"),
    path(
        "challenge/<slug:challenge_slug>/submit/",
        views.submit_challenge,
        name="submit_challenge",
    ),
    path(
        "challenge/<slug:challenge_slug>/move/<str:direction>/",
        views.move_challenge,
        name="move_challenge",
    ),
    path("analytics/", views.analytics_view, name="analytics"),
    path("analytics/switch-class/", views.switch_class_view, name="switch_class"),
    path(
        "analytics/submission/<int:submission_id>/",
        views.submission_detail_view,
        name="submission_detail",
    ),
    path("analytics/user/<str:username>/", views.user_detail_view, name="user_detail"),
    path(
        "analytics/challenge/<slug:challenge_slug>/",
        views.challenge_detail_view,
        name="challenge_detail",
    ),
    path("logout/", views.logout_view, name="logout"),
    path("links/", views.links_view, name="links"),
    path("links/<int:link_id>/revoke/", views.revoke_link_view, name="revoke_link"),
    path("s/<slug:alias>/", views.short_link_redirect_view, name="short_link_redirect"),
]
