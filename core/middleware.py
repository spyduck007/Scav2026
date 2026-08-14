"""Rate limits short-link redirects (and any unknown path) per IP.

This exists to blunt brute-forcing of short link aliases: rather than
maintaining a whitelist of "known good" paths that has to be kept in sync
with every new page, every real page in the app is implicitly exempt, and
only two things get throttled: the short-link redirect view itself, and
paths that don't resolve to anything at all.
"""

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.urls import Resolver404, resolve

from .views import SESSION_PARTICIPANT_KEY, short_link_redirect_view


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


class ShortLinkRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_throttle(request):
            cache_key = f"linkrate:{_client_ip(request)}"
            if cache.get(cache_key):
                return HttpResponse(
                    "Too many requests. Please wait a few seconds and try again.",
                    status=429,
                    content_type="text/plain",
                )
            window = getattr(settings, "SCAV_LINK_RATE_LIMIT_SECONDS", 4)
            cache.set(cache_key, True, timeout=window)

        return self.get_response(request)

    def _should_throttle(self, request) -> bool:
        try:
            match = resolve(request.path_info)
        except Resolver404:
            return True

        if match.func != short_link_redirect_view:
            return False

        # Exempt logged-in admins/scavcomm so they can manage and test links
        # without tripping their own anti-brute-force protection.
        from .models import Participant

        participant_id = request.session.get(SESSION_PARTICIPANT_KEY)
        if participant_id:
            participant = (
                Participant.objects.filter(pk=participant_id)
                .only("is_admin", "is_scavcomm")
                .first()
            )
            if participant and (participant.is_admin or participant.is_scavcomm):
                return False

        return True
