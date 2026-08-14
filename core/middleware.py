"""Rate limits short-link redirects (and any unknown path), per participant.

This exists to blunt brute-forcing of short link aliases: rather than
maintaining a whitelist of "known good" paths that has to be kept in sync
with every new page, every real page in the app is implicitly exempt, and
only two things get throttled: the short-link redirect view itself, and
paths that don't resolve to anything at all.

Throttling is keyed by the logged-in participant rather than IP, since a
lot of participants share the same IP on school wifi (NAT), and an IP-based
limit would throttle everyone behind it together. Anonymous requests (no
session yet) fall back to IP, since there's nothing else to key them by --
but note that an anonymous visitor can never actually distinguish a valid
alias from an invalid one anyway, since short_link_redirect_view sends
every anonymous request to login before it ever looks the alias up.
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
        throttle_key = self._throttle_key(request)
        if throttle_key is not None:
            cache_key = f"linkrate:{throttle_key}"
            if cache.get(cache_key):
                return HttpResponse(
                    "Too many requests. Please wait a few seconds and try again.",
                    status=429,
                    content_type="text/plain",
                )
            window = getattr(settings, "SCAV_LINK_RATE_LIMIT_SECONDS", 4)
            cache.set(cache_key, True, timeout=window)

        return self.get_response(request)

    def _throttle_key(self, request) -> str | None:
        """Return the cache key to rate-limit by, or None to skip throttling."""

        try:
            match = resolve(request.path_info)
        except Resolver404:
            pass  # genuinely unknown path -> still throttle, below
        else:
            if match.func != short_link_redirect_view:
                return None  # a real page, never throttled

        participant_id = request.session.get(SESSION_PARTICIPANT_KEY)
        if not participant_id:
            return f"ip:{_client_ip(request)}"

        # Exempt logged-in admins/scavcomm so they can manage and test links
        # without tripping their own anti-brute-force protection.
        from .models import Participant

        participant = (
            Participant.objects.filter(pk=participant_id)
            .only("is_admin", "is_scavcomm")
            .first()
        )
        if participant and (participant.is_admin or participant.is_scavcomm):
            return None

        return f"user:{participant_id}"
