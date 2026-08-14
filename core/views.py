"""Views for the Scavenger Hunt core app."""

import json
import re
from collections import defaultdict
from datetime import timedelta
from math import ceil
from typing import Any

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    get_user_model,
    login as auth_login,
    logout as auth_logout,
)
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Prefetch, Sum
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from requests_oauthlib import OAuth2Session

from .models import (
    Challenge,
    ChallengeCategory,
    ChallengeSolve,
    ChallengeSubmission,
    DiscordSettings,
    Participant,
    ShortLink,
)

ALIAS_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
POST_LOGIN_REDIRECT_SESSION_KEY = "post_login_redirect"


SESSION_STATE_KEY = "ion_oauth_state"
SESSION_TOKEN_KEY = "ion_oauth_token"
SESSION_PARTICIPANT_KEY = "ion_participant_id"
SESSION_SUBMISSION_KEY_PREFIX = "challenge_last_submission_at"


def _missing_oauth_settings(require_secret: bool = False) -> list[str]:
    required = {
        "ION_CLIENT_ID": settings.ION_CLIENT_ID,
        "ION_REDIRECT_URI": settings.ION_REDIRECT_URI,
    }
    if require_secret:
        required["ION_CLIENT_SECRET"] = settings.ION_CLIENT_SECRET
    return [key for key, value in required.items() if not value]


def _create_oauth_session(state: str | None = None) -> OAuth2Session:
    return OAuth2Session(
        settings.ION_CLIENT_ID,
        redirect_uri=settings.ION_REDIRECT_URI,
        scope=settings.ION_SCOPE,
        state=state,
    )


def _extract_graduation_year(profile: dict[str, Any]) -> int | None:
    value = profile.get("graduation_year")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_is_admin(profile: dict[str, Any]) -> bool:
    groups = profile.get("groups") or []
    if isinstance(groups, (list, tuple, set)):
        groups = {str(item).lower() for item in groups}
        if {"scavenger-admin", "ion-admin", "admin"} & groups:
            return True
    return bool(profile.get("is_admin") or profile.get("is_staff"))


def _build_display_name(profile: dict[str, Any]) -> str:
    first = (profile.get("first_name") or "").strip()
    last = (profile.get("last_name") or "").strip()
    if first or last:
        return (first + " " + last).strip()
    return profile.get("ion_username", "").strip()


def _get_logged_in_participant(request) -> Participant | None:
    participant_id = request.session.get(SESSION_PARTICIPANT_KEY)
    if not participant_id:
        return None
    try:
        return Participant.objects.get(pk=participant_id)
    except Participant.DoesNotExist:
        request.session.pop(SESSION_PARTICIPANT_KEY, None)
        return None


def _format_est(dt: timezone.datetime | None) -> str | None:
    if dt is None:
        return None
    localized = dt.astimezone(settings.SCAV_HUNT_TZ)
    return formats.date_format(localized, "N j, Y g:i A") + " ET"


def _hunt_window_status() -> tuple[bool, str, str]:
    now = timezone.now().astimezone(settings.SCAV_HUNT_TZ)
    start = settings.SCAV_HUNT_START
    end = settings.SCAV_HUNT_END

    if start and now < start:
        message = "The hunt hasn't opened yet. Doors open on {when}.".format(
            when=_format_est(start)
        )
        return False, "upcoming", message

    if end and now > end:
        message = "The hunt has ended. It closed on {when}.".format(
            when=_format_est(end)
        )
        return False, "ended", message

    return True, "open", ""


def _build_leaderboard(user_year: int | None) -> list[dict[str, Any]]:
    team_years = settings.SCAV_HUNT_TEAM_YEARS

    member_aggregates = (
        Participant.objects.filter(graduation_year__in=team_years)
        .values("graduation_year")
        .annotate(member_count=Count("id"))
    )
    member_counts = {
        item["graduation_year"]: item["member_count"] for item in member_aggregates
    }

    score_aggregates = (
        ChallengeSolve.objects.filter(team_year__in=team_years)
        .values("team_year")
        .annotate(total_points=Sum("awarded_points"))
    )
    scores = {item["team_year"]: item["total_points"] or 0 for item in score_aggregates}

    leaderboard: list[dict[str, Any]] = []
    for year in team_years:
        leaderboard.append(
            {
                "year": year,
                "label": f"Class of {year}",
                "score": int(scores.get(year, 0) or 0),
                "member_count": member_counts.get(year, 0),
                "is_user_team": year == user_year,
            }
        )

    leaderboard.sort(key=lambda item: (-item["score"], item["year"]))
    last_score = None
    current_rank = 0
    for position, row in enumerate(leaderboard, start=1):
        if row["score"] != last_score:
            current_rank = position
            last_score = row["score"]
        row["rank"] = current_rank
    return leaderboard


def _build_challenge_catalog(participant: Participant) -> dict[str, Any]:
    team_year = participant.graduation_year
    team_years = set(settings.SCAV_HUNT_TEAM_YEARS)
    team_year_valid = team_year in team_years if team_year is not None else False

    challenge_prefetch = Prefetch(
        "challenges",
        queryset=Challenge.objects.filter(is_active=True)
        .select_related("category")
        .prefetch_related(
            Prefetch("prerequisites", queryset=Challenge.objects.only("id", "title")),
            Prefetch(
                "solves", queryset=ChallengeSolve.objects.select_related("participant")
            ),
        )
        .order_by("sort_order", "title"),
    )

    categories = (
        ChallengeCategory.objects.filter(is_active=True)
        .order_by("sort_order", "name")
        .prefetch_related(challenge_prefetch)
    )

    categories_payload: list[dict[str, Any]] = []
    solved_ids_by_team: set[int] = set()
    awarded_points_for_team: dict[int, int] = {}

    if team_year_valid:
        team_solves = ChallengeSolve.objects.filter(team_year=team_year)
        solved_ids_by_team = {solve.challenge_id for solve in team_solves}
        awarded_points_for_team = {
            solve.challenge_id: solve.awarded_points for solve in team_solves
        }

    for category in categories:
        challenge_cards: list[dict[str, Any]] = []
        category_challenges = list(category.challenges.all())
        total_in_category = len(category_challenges)
        for index, challenge in enumerate(category_challenges):
            solves = list(challenge.solves.all())
            total_solves = len(solves)
            user_has_solved = challenge.id in solved_ids_by_team
            prerequisites = list(challenge.prerequisites.all())

            prerequisites_met = True
            prerequisite_entries: list[dict[str, Any]] = []
            if prerequisites:
                for prereq in prerequisites:
                    met = True
                    if challenge.requires_dependencies():
                        if not team_year_valid:
                            met = False
                            prerequisites_met = False
                        else:
                            met = prereq.id in solved_ids_by_team
                            if not met:
                                prerequisites_met = False
                    prerequisite_entries.append({"title": prereq.title, "met": met})
            elif challenge.requires_dependencies():
                prerequisites_met = (
                    True  # No prerequisites defined means nothing blocks submission
                )

            exclusive_locked = False
            if challenge.is_exclusive():
                exclusive_locked = total_solves > 0 and not user_has_solved

            can_submit = (
                team_year_valid
                and not user_has_solved
                and not exclusive_locked
                and (not challenge.requires_dependencies() or prerequisites_met)
            )

            current_points = (
                awarded_points_for_team.get(challenge.id)
                if user_has_solved
                else challenge.points_for_next_solve(total_solves)
            )

            solves_summary = sorted(
                (
                    {
                        "team_year": solve.team_year,
                        "awarded_points": solve.awarded_points,
                        "by_user_team": team_year_valid
                        and solve.team_year == team_year,
                    }
                    for solve in solves
                ),
                key=lambda entry: (-entry["awarded_points"], entry["team_year"]),
            )

            challenge_cards.append(
                {
                    "id": challenge.id,
                    "slug": challenge.slug,
                    "title": challenge.title,
                    "description": challenge.description,
                    "challenge_type": challenge.challenge_type,
                    "type_label": challenge.get_challenge_type_display(),
                    "base_points": challenge.base_points,
                    "current_points": current_points,
                    "has_decay": challenge.is_decreasing(),
                    "decay_percent": float(challenge.decay_percent),
                    "minimum_points": challenge.minimum_points,
                    "is_exclusive": challenge.is_exclusive(),
                    "requires_dependencies": challenge.requires_dependencies(),
                    "prerequisites": prerequisite_entries,
                    "exclusive_locked": exclusive_locked,
                    "prerequisites_met": prerequisites_met,
                    "user_has_solved": user_has_solved,
                    "can_submit": can_submit,
                    "solves_count": total_solves,
                    "solves_summary": solves_summary,
                    "sort_order": challenge.sort_order,
                    "admin_url": reverse(
                        "admin:core_challenge_change", args=[challenge.pk]
                    ),
                    "can_move_left": index > 0,
                    "can_move_right": index < total_in_category - 1,
                    "move_left_url": reverse(
                        "core:move_challenge", args=[challenge.slug, "left"]
                    ),
                    "move_right_url": reverse(
                        "core:move_challenge", args=[challenge.slug, "right"]
                    ),
                }
            )

        categories_payload.append(
            {
                "id": category.id,
                "name": category.name,
                "slug": category.slug,
                "description": category.description,
                "challenges": challenge_cards,
            }
        )

    return {
        "categories": categories_payload,
        "team_year": team_year if team_year_valid else None,
        "team_label": f"Class of {team_year}" if team_year_valid else None,
        "team_year_valid": team_year_valid,
    }


def _countdown_context(participant: Participant, is_open: bool) -> dict[str, Any]:
    end = settings.SCAV_HUNT_END
    if not end:
        return {"show_countdown": False, "countdown_target": None}

    end_est = end.astimezone(settings.SCAV_HUNT_TZ)
    now_est = timezone.now().astimezone(settings.SCAV_HUNT_TZ)

    # Countdown is hidden for privileged users outside the active window.
    allow_for_privileged = participant.can_bypass_hunt_window() and is_open
    show_countdown = bool(
        is_open
        and (not participant.can_bypass_hunt_window() or allow_for_privileged)
        and end_est > now_est
    )

    return {
        "show_countdown": show_countdown,
        "countdown_target": end_est.isoformat(),
    }


def _send_discord_first_blood(
    challenge: Challenge, participant: Participant, team_year: int, awarded_points: int
) -> None:
    """Send a Discord webhook notification for first blood."""
    try:
        discord_settings = DiscordSettings.load()
        if (
            not discord_settings.notifications_enabled
            or not discord_settings.webhook_url
        ):
            return

        # Create a rich embed for the notification
        embed = {
            "title": "🩸 FIRST BLOOD! 🩸",
            "description": f"**{challenge.title}** has been solved for the first time!",
            "color": 15158332,  # Red color
            "fields": [
                {"name": "Challenge", "value": challenge.title, "inline": True},
                {"name": "Solved By", "value": f"Class of {team_year}", "inline": True},
                {
                    "name": "Points Awarded",
                    "value": str(awarded_points),
                    "inline": True,
                },
                {"name": "Category", "value": challenge.category.name, "inline": True},
                {
                    "name": "Challenge Type",
                    "value": challenge.get_challenge_type_display(),
                    "inline": True,
                },
                {
                    "name": "Solver",
                    "value": participant.display_name or participant.ion_username,
                    "inline": True,
                },
            ],
            "timestamp": timezone.now().isoformat(),
            "footer": {"text": "Scavenger Hunt"},
        }

        payload = {
            "embeds": [embed],
            "username": "First Blood Bot",
        }

        response = requests.post(
            discord_settings.webhook_url,
            json=payload,
            timeout=5,
        )
        response.raise_for_status()
    except Exception:
        # Silently fail to avoid disrupting the solve process
        pass


def login_view(request):
    """Render the landing/login page."""

    participant = _get_logged_in_participant(request)
    if participant:
        return redirect("core:challenge")

    missing_settings = _missing_oauth_settings()
    context = {
        "missing_settings": missing_settings,
        "ion_scope": settings.ION_SCOPE,
        "ion_ready": not missing_settings,
    }
    return render(request, "core/login.html", context)


def oauth_start(request):
    """Start the Ion OAuth2 flow by redirecting to the provider."""

    missing_settings = _missing_oauth_settings()
    if missing_settings:
        raise ImproperlyConfigured(
            "Ion OAuth is not fully configured. Missing: " + ", ".join(missing_settings)
        )

    oauth = _create_oauth_session()
    authorization_url, state = oauth.authorization_url(settings.ION_AUTHORIZE_URL)
    request.session[SESSION_STATE_KEY] = state
    return HttpResponseRedirect(authorization_url)


def oauth_callback(request):
    """Handle the Ion OAuth2 callback and greet the authenticated user."""

    missing_settings = _missing_oauth_settings(require_secret=True)
    if missing_settings:
        raise ImproperlyConfigured(
            "Ion OAuth is not fully configured. Missing: " + ", ".join(missing_settings)
        )

    state = request.GET.get("state")
    code = request.GET.get("code")
    stored_state = request.session.pop(SESSION_STATE_KEY, None)

    if not state or state != stored_state:
        return HttpResponseBadRequest("Invalid OAuth state returned by Ion.")

    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    oauth = _create_oauth_session(state=state)

    try:
        token = oauth.fetch_token(
            settings.ION_TOKEN_URL,
            code=code,
            client_secret=settings.ION_CLIENT_SECRET,
        )
    except Exception as exc:  # pragma: no cover - network errors
        return HttpResponse(
            "Unable to complete authentication with Ion at this time.", status=502
        )

    request.session[SESSION_TOKEN_KEY] = token

    try:
        profile_response = oauth.get(settings.ION_PROFILE_URL)
        profile_response.raise_for_status()
        profile_data = profile_response.json()
    except Exception as exc:  # pragma: no cover - network errors
        return HttpResponse("Unable to load Ion profile data.", status=502)

    ion_username = profile_data.get("ion_username")
    if not ion_username:
        return HttpResponse("Ion did not return a username.", status=502)

    display_name = _build_display_name(profile_data) or ion_username

    profile_email = profile_data.get("tj_email") or profile_data.get("email", "")
    profile_grad_year = _extract_graduation_year(profile_data)
    profile_is_admin = _extract_is_admin(profile_data)

    participant, created_participant = Participant.objects.get_or_create(
        ion_username=ion_username,
        defaults={
            "display_name": display_name,
            "email": profile_email,
            "graduation_year": profile_grad_year,
            "is_admin": profile_is_admin,
            "last_login": timezone.now(),
        },
    )

    if not created_participant:
        participant_updates: list[str] = []

        if participant.display_name != display_name:
            participant.display_name = display_name
            participant_updates.append("display_name")

        if participant.email != profile_email:
            participant.email = profile_email
            participant_updates.append("email")

        if participant.graduation_year != profile_grad_year:
            participant.graduation_year = profile_grad_year
            participant_updates.append("graduation_year")

        if profile_is_admin and not participant.is_admin:
            participant.is_admin = True
            participant_updates.append("is_admin")

        participant.last_login = timezone.now()
        participant_updates.append("last_login")

        if participant_updates:
            participant.save(update_fields=list(set(participant_updates)))

    request.session[SESSION_PARTICIPANT_KEY] = participant.pk

    user_model = get_user_model()
    user_defaults = {
        "email": participant.email,
        "first_name": profile_data.get("first_name", ""),
        "last_name": profile_data.get("last_name", ""),
    }
    user, created_user = user_model.objects.get_or_create(
        username=ion_username,
        defaults=user_defaults,
    )

    desired_is_staff = participant.is_admin
    desired_is_superuser = participant.is_admin

    updated_fields = []
    for field, desired_value in user_defaults.items():
        if getattr(user, field, "") != (desired_value or ""):
            setattr(user, field, desired_value or "")
            updated_fields.append(field)

    if user.is_staff != desired_is_staff:
        user.is_staff = desired_is_staff
        updated_fields.append("is_staff")

    if user.is_superuser != desired_is_superuser:
        user.is_superuser = desired_is_superuser
        updated_fields.append("is_superuser")

    if created_user and not user.has_usable_password():
        user.set_unusable_password()
        updated_fields.append("password")

    if updated_fields:
        user.save(update_fields=list(set(updated_fields)))

    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    next_url = request.session.pop(POST_LOGIN_REDIRECT_SESSION_KEY, None)
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)

    return redirect("core:challenge")


def dashboard_view(request):
    """Show a minimal dashboard for authenticated participants."""

    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    context = {
        "participant": participant,
    }
    return render(request, "core/dashboard.html", context)


def rules_view(request):
    """Show the Scavenger Hunt terms & conditions."""

    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    context = {
        "participant": participant,
    }
    return render(request, "core/rules.html", context)


def challenge_view(request):
    """Display the challenge page or a closed notice based on hunt status."""

    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    is_open, state, message = _hunt_window_status()
    hunt_has_ended = state == "ended"

    # Only build challenge catalog if the hunt is open or user can bypass
    challenge_catalog = {}
    if is_open or participant.can_bypass_hunt_window():
        challenge_catalog = _build_challenge_catalog(participant)

    # Always build leaderboard for the closed template
    leaderboard = _build_leaderboard(participant.graduation_year)

    base_context = {
        "participant": participant,
        "hunt_state": state,
        "hunt_has_ended": hunt_has_ended,
        "hunt_message": message,
        "hunt_starts_at": _format_est(settings.SCAV_HUNT_START),
        "hunt_ends_at": _format_est(settings.SCAV_HUNT_END),
        "leaderboard": leaderboard,
        "now_year": timezone.now().astimezone(settings.SCAV_HUNT_TZ).year,
    }

    # Only add challenge catalog if we built it
    if challenge_catalog:
        base_context["challenge_catalog"] = challenge_catalog

    base_context.update(_countdown_context(participant, is_open))

    if is_open or participant.can_bypass_hunt_window():
        return render(request, "core/challenge.html", base_context)

    return render(request, "core/challenge_closed.html", base_context, status=403)


def _wants_json_response(request) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _submission_response(request, *, status: str, message: str, payload: dict | None = None):
    """Return JSON for the async answer form, or fall back to message+redirect."""
    if _wants_json_response(request):
        data = {"status": status, "message": message}
        if payload:
            data.update(payload)
        return JsonResponse(data)

    level = {
        "correct": messages.SUCCESS,
        "already_solved": messages.INFO,
    }.get(status, messages.ERROR)
    messages.add_message(request, level, message)
    return redirect("core:challenge")


@require_POST
def submit_challenge(request, challenge_slug: str):
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    is_open, _state, hunt_message = _hunt_window_status()
    if not is_open and not participant.can_bypass_hunt_window():
        return _submission_response(
            request,
            status="error",
            message=hunt_message or "Challenge submissions are not available right now.",
        )

    team_year = participant.graduation_year
    team_years = set(settings.SCAV_HUNT_TEAM_YEARS)

    if team_year is None or team_year not in team_years:
        return _submission_response(
            request,
            status="error",
            message="You do not have an assigned class year for the scavenger hunt. "
            "Please contact an organizer to be added to a team.",
        )

    cooldown_seconds = getattr(settings, "SCAV_SUBMISSION_COOLDOWN_SECONDS", 0)
    submission_session_key = f"{SESSION_SUBMISSION_KEY_PREFIX}:{participant.pk}"
    if cooldown_seconds > 0:
        last_attempt_raw = request.session.get(submission_session_key)
        if last_attempt_raw:
            try:
                last_attempt = timezone.datetime.fromisoformat(last_attempt_raw)
            except ValueError:
                last_attempt = None
            if last_attempt is not None:
                if timezone.is_naive(last_attempt):
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
                next_allowed = last_attempt + timedelta(seconds=cooldown_seconds)
                now = timezone.now()
                if now < next_allowed:
                    remaining_seconds = max(
                        1, ceil((next_allowed - now).total_seconds())
                    )
                    return _submission_response(
                        request,
                        status="error",
                        message="Please wait {seconds} more second{plural} before submitting another answer.".format(
                            seconds=remaining_seconds,
                            plural="s" if remaining_seconds != 1 else "",
                        ),
                    )

    submitted_answer = (request.POST.get("answer") or "").strip()
    if not submitted_answer:
        return _submission_response(
            request, status="error", message="Please enter an answer before submitting."
        )

    with transaction.atomic():
        challenge = get_object_or_404(
            Challenge.objects.select_for_update()
            .select_related("category")
            .prefetch_related("prerequisites"),
            slug=challenge_slug,
            is_active=True,
            category__is_active=True,
        )

        existing_team_solve = ChallengeSolve.objects.filter(
            challenge=challenge, team_year=team_year
        ).first()
        if existing_team_solve:
            return _submission_response(
                request,
                status="already_solved",
                message="Your class has already solved this challenge and earned "
                f"{existing_team_solve.awarded_points} points.",
                payload={
                    "challenge": {
                        "slug": challenge.slug,
                        "awarded_points": existing_team_solve.awarded_points,
                    }
                },
            )

        if challenge.is_exclusive():
            if ChallengeSolve.objects.filter(challenge=challenge).exists():
                return _submission_response(
                    request,
                    status="error",
                    message="This exclusive challenge has already been claimed by another class.",
                )

        if challenge.requires_dependencies():
            prerequisites = challenge.prerequisites.all()
            unmet = prerequisites.exclude(solves__team_year=team_year)
            if unmet.exists():
                return _submission_response(
                    request,
                    status="error",
                    message="You must solve all prerequisite challenges before attempting this one.",
                )

        if cooldown_seconds > 0:
            request.session[submission_session_key] = timezone.now().isoformat()

        expected_answer = challenge.answer
        answers_match = False
        if challenge.answer_case_sensitive:
            answers_match = submitted_answer == expected_answer
        else:
            answers_match = (
                submitted_answer.casefold() == expected_answer.strip().casefold()
            )

        if not answers_match:
            ChallengeSubmission.objects.create(
                challenge=challenge,
                participant=participant,
                team_year=team_year,
                submitted_answer=submitted_answer,
                is_correct=False,
            )
            return _submission_response(
                request,
                status="incorrect",
                message="Sorry, that answer is not correct. Try again!",
            )

        solves_count = ChallengeSolve.objects.filter(challenge=challenge).count()
        awarded_points = challenge.points_for_next_solve(solves_count)
        is_first_blood = solves_count == 0

        solve = ChallengeSolve.objects.create(
            challenge=challenge,
            participant=participant,
            team_year=team_year,
            awarded_points=awarded_points,
            submitted_answer=submitted_answer,
        )
        ChallengeSubmission.objects.create(
            challenge=challenge,
            participant=participant,
            team_year=team_year,
            submitted_answer=submitted_answer,
            is_correct=True,
            awarded_points=awarded_points,
            solve=solve,
        )

        if is_first_blood:
            _send_discord_first_blood(challenge, participant, team_year, awarded_points)

        # If another challenge depends on this one, its unlock state can only
        # be recomputed by a full catalog rebuild, so tell the client to
        # refresh instead of trying to patch the DOM in place.
        requires_refresh = challenge.unlocking_challenges.filter(is_active=True).exists()

        response = _submission_response(
            request,
            status="correct",
            message=f"Challenge solved! {awarded_points} points awarded to the Class of {team_year}.",
            payload={
                "challenge": {
                    "slug": challenge.slug,
                    "awarded_points": awarded_points,
                    "solves_count": solves_count + 1,
                    "is_first_blood": is_first_blood,
                    "requires_refresh": requires_refresh,
                },
                "leaderboard": _build_leaderboard(participant.graduation_year),
            },
        )

    return response


@require_POST
def logout_view(request):
    """Clear OAuth-related session data and send the user back to login."""

    auth_logout(request)
    for key in [SESSION_STATE_KEY, SESSION_TOKEN_KEY, SESSION_PARTICIPANT_KEY]:
        request.session.pop(key, None)

    return redirect("core:login")


@require_POST
def move_challenge(request, challenge_slug: str, direction: str):
    participant = _get_logged_in_participant(request)
    if not participant or not participant.is_admin:
        messages.error(request, "Admin privileges are required to reorder challenges.")
        return redirect("core:challenge")

    if direction not in {"left", "right"}:
        return HttpResponseBadRequest("Invalid move direction.")

    challenge = get_object_or_404(
        Challenge.objects.select_related("category"),
        slug=challenge_slug,
    )

    category = challenge.category
    ordered = list(
        Challenge.objects.filter(category=category).order_by(
            "sort_order", "title", "pk"
        )
    )

    try:
        index = next(i for i, item in enumerate(ordered) if item.pk == challenge.pk)
    except StopIteration:
        messages.error(request, "Unable to locate that challenge for reordering.")
        return redirect("core:challenge")

    if direction == "left":
        if index == 0:
            messages.info(
                request, "This challenge is already at the start of the list."
            )
            return redirect("core:challenge")
        ordered[index - 1], ordered[index] = ordered[index], ordered[index - 1]
    else:  # direction == "right"
        if index == len(ordered) - 1:
            messages.info(request, "This challenge is already at the end of the list.")
            return redirect("core:challenge")
        ordered[index + 1], ordered[index] = ordered[index], ordered[index + 1]

    with transaction.atomic():
        for position, item in enumerate(ordered, start=1):
            if item.sort_order != position:
                Challenge.objects.filter(pk=item.pk).update(sort_order=position)

    return redirect("core:challenge")


SESSION_ADMIN_VIEW_AS_CLASS = "admin_view_as_class"


def analytics_view(request):
    """Display analytics dashboard for admins and scavcomm members."""
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to access this page.")
        return redirect("core:challenge")

    team_years = settings.SCAV_HUNT_TEAM_YEARS

    # Admin class view switching
    admin_view_as_class = request.session.get(SESSION_ADMIN_VIEW_AS_CLASS)
    if admin_view_as_class and admin_view_as_class not in team_years:
        admin_view_as_class = None
        request.session.pop(SESSION_ADMIN_VIEW_AS_CLASS, None)

    # Submission logs - every graded attempt, correct or not
    submission_logs = ChallengeSubmission.objects.select_related(
        "challenge", "challenge__category", "participant"
    ).order_by("-created_at")[:100]

    submission_data = []
    for submission in submission_logs:
        is_first_blood = False
        if submission.is_correct and submission.solve_id:
            first_solve = (
                ChallengeSolve.objects.filter(challenge=submission.challenge)
                .order_by("created_at")
                .first()
            )
            is_first_blood = bool(first_solve) and first_solve.id == submission.solve_id

        submission_data.append(
            {
                "id": submission.id,
                "challenge_title": submission.challenge.title,
                "challenge_slug": submission.challenge.slug,
                "category_name": submission.challenge.category.name,
                "participant_name": submission.participant.display_name
                or submission.participant.ion_username,
                "participant_username": submission.participant.ion_username,
                "team_year": submission.team_year,
                "is_correct": submission.is_correct,
                "awarded_points": submission.awarded_points,
                "submitted_answer": submission.submitted_answer,
                "created_at": submission.created_at.astimezone(settings.SCAV_HUNT_TZ),
                "is_first_blood": is_first_blood,
            }
        )

    # Class-by-class stats
    class_stats = []
    for year in team_years:
        participants_in_class = Participant.objects.filter(graduation_year=year)
        total_members = participants_in_class.count()

        # Get solves by this class
        class_solves = ChallengeSolve.objects.filter(team_year=year)
        total_points = class_solves.aggregate(total=Sum("awarded_points"))["total"] or 0
        total_solves = class_solves.count()

        # Top contributors in this class (participants who submitted winning answers)
        top_contributors = (
            ChallengeSolve.objects.filter(team_year=year)
            .values(
                "participant__id",
                "participant__display_name",
                "participant__ion_username",
            )
            .annotate(
                points_contributed=Sum("awarded_points"),
                solves_count=Count("id"),
            )
            .order_by("-points_contributed")[:5]
        )

        contributors_list = []
        for contrib in top_contributors:
            contributors_list.append(
                {
                    "name": contrib["participant__display_name"]
                    or contrib["participant__ion_username"],
                    "username": contrib["participant__ion_username"],
                    "points": contrib["points_contributed"],
                    "solves": contrib["solves_count"],
                }
            )

        # Recent activity for this class
        recent_solves = class_solves.order_by("-created_at")[:5]
        recent_activity = []
        for solve in recent_solves:
            recent_activity.append(
                {
                    "challenge": solve.challenge.title,
                    "challenge_slug": solve.challenge.slug,
                    "solver": solve.participant.display_name
                    or solve.participant.ion_username,
                    "solver_username": solve.participant.ion_username,
                    "points": solve.awarded_points,
                    "time": solve.created_at.astimezone(settings.SCAV_HUNT_TZ),
                }
            )

        class_stats.append(
            {
                "year": year,
                "label": f"Class of {year}",
                "total_members": total_members,
                "total_points": total_points,
                "total_solves": total_solves,
                "top_contributors": contributors_list,
                "recent_activity": recent_activity,
            }
        )

    # Sort by points descending
    class_stats.sort(key=lambda x: -x["total_points"])

    # Challenge statistics
    challenges = Challenge.objects.filter(is_active=True).select_related("category")
    challenge_stats = []
    for challenge in challenges:
        solves = ChallengeSolve.objects.filter(challenge=challenge)
        solve_count = solves.count()
        first_blood = solves.order_by("created_at").first()

        challenge_stats.append(
            {
                "id": challenge.id,
                "title": challenge.title,
                "slug": challenge.slug,
                "category": challenge.category.name,
                "type": challenge.get_challenge_type_display(),
                "base_points": challenge.base_points,
                "solve_count": solve_count,
                "first_blood_team": f"Class of {first_blood.team_year}"
                if first_blood
                else None,
                "first_blood_solver": (
                    first_blood.participant.display_name
                    or first_blood.participant.ion_username
                )
                if first_blood
                else None,
                "first_blood_username": first_blood.participant.ion_username
                if first_blood
                else None,
                "first_blood_time": first_blood.created_at.astimezone(
                    settings.SCAV_HUNT_TZ
                )
                if first_blood
                else None,
            }
        )

    # Overall statistics
    total_participants = Participant.objects.count()
    total_submissions = ChallengeSubmission.objects.count()
    correct_submissions = ChallengeSubmission.objects.filter(is_correct=True).count()
    submission_accuracy = (
        round((correct_submissions / total_submissions) * 100, 1)
        if total_submissions
        else 0
    )
    total_points_awarded = (
        ChallengeSolve.objects.aggregate(total=Sum("awarded_points"))["total"] or 0
    )
    total_challenges = Challenge.objects.filter(is_active=True).count()
    solved_challenges = (
        Challenge.objects.filter(is_active=True, solves__isnull=False)
        .distinct()
        .count()
    )

    # Leaderboard
    viewed_team_year = admin_view_as_class or participant.graduation_year
    leaderboard = _build_leaderboard(viewed_team_year)

    context = {
        "participant": participant,
        "submission_logs": submission_data,
        "class_stats": class_stats,
        "challenge_stats": challenge_stats,
        "leaderboard": leaderboard,
        "team_years": team_years,
        "admin_view_as_class": admin_view_as_class,
        "overall_stats": {
            "total_participants": total_participants,
            "total_submissions": total_submissions,
            "correct_submissions": correct_submissions,
            "submission_accuracy": submission_accuracy,
            "total_points_awarded": total_points_awarded,
            "total_challenges": total_challenges,
            "solved_challenges": solved_challenges,
            "unsolved_challenges": total_challenges - solved_challenges,
        },
        "hunt_starts_at": _format_est(settings.SCAV_HUNT_START),
        "hunt_ends_at": _format_est(settings.SCAV_HUNT_END),
    }

    return render(request, "core/analytics.html", context)


@require_POST
def switch_class_view(request):
    """Allow admins/scavcomm to switch their view to a different class for troubleshooting."""
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to perform this action.")
        return redirect("core:challenge")

    team_years = settings.SCAV_HUNT_TEAM_YEARS
    selected_year = request.POST.get("class_year")

    if selected_year == "reset":
        request.session.pop(SESSION_ADMIN_VIEW_AS_CLASS, None)
        messages.success(request, "View reset to your original class.")
    else:
        try:
            year_int = int(selected_year)
            if year_int in team_years:
                request.session[SESSION_ADMIN_VIEW_AS_CLASS] = year_int
                messages.success(request, f"Now viewing as Class of {year_int}.")
            else:
                messages.error(request, "Invalid class year selected.")
        except (TypeError, ValueError):
            messages.error(request, "Invalid class year selected.")

    return redirect("core:analytics")


def submission_detail_view(request, submission_id: int):
    """Display detailed information about a specific submission attempt."""
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to access this page.")
        return redirect("core:challenge")

    submission = get_object_or_404(
        ChallengeSubmission.objects.select_related(
            "challenge", "challenge__category", "participant"
        ),
        pk=submission_id,
    )

    # Check if this was first blood (only meaningful for correct submissions)
    is_first_blood = False
    if submission.is_correct and submission.solve_id:
        first_solve = (
            ChallengeSolve.objects.filter(challenge=submission.challenge)
            .order_by("created_at")
            .first()
        )
        is_first_blood = bool(first_solve) and first_solve.id == submission.solve_id

    # Get other submission attempts for the same challenge
    other_submissions = (
        ChallengeSubmission.objects.filter(challenge=submission.challenge)
        .exclude(pk=submission.pk)
        .select_related("participant")
        .order_by("created_at")
    )

    # Get other submission attempts by the same participant
    participant_other_submissions = (
        ChallengeSubmission.objects.filter(participant=submission.participant)
        .exclude(pk=submission.pk)
        .select_related("challenge", "challenge__category")
        .order_by("-created_at")[:10]
    )

    context = {
        "participant": participant,
        "submission": submission,
        "is_first_blood": is_first_blood,
        "other_submissions": other_submissions,
        "participant_other_submissions": participant_other_submissions,
        "submission_time": submission.created_at.astimezone(settings.SCAV_HUNT_TZ),
    }

    return render(request, "core/submission_detail.html", context)


def user_detail_view(request, username: str):
    """Display detailed analytics about a specific user."""
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to access this page.")
        return redirect("core:challenge")

    target_user = get_object_or_404(Participant, ion_username=username)

    # Get all solves by this user
    user_solves = (
        ChallengeSolve.objects.filter(participant=target_user)
        .select_related("challenge", "challenge__category")
        .order_by("-created_at")
    )

    # Calculate statistics
    total_points = user_solves.aggregate(total=Sum("awarded_points"))["total"] or 0
    total_solves = user_solves.count()

    # First bloods
    first_bloods = []
    for solve in user_solves:
        first_solve = (
            ChallengeSolve.objects.filter(challenge=solve.challenge)
            .order_by("created_at")
            .first()
        )
        if first_solve and first_solve.id == solve.id:
            first_bloods.append(solve)

    # Categories breakdown
    category_stats = (
        user_solves.values("challenge__category__name")
        .annotate(
            solve_count=Count("id"),
            points=Sum("awarded_points"),
        )
        .order_by("-points")
    )

    # Challenge types breakdown
    type_stats = (
        user_solves.values("challenge__challenge_type")
        .annotate(
            solve_count=Count("id"),
            points=Sum("awarded_points"),
        )
        .order_by("-points")
    )

    # Get team info
    team_year = target_user.graduation_year
    team_years = settings.SCAV_HUNT_TEAM_YEARS
    is_valid_team = team_year in team_years if team_year else False

    # Team rank if applicable
    team_rank = None
    if is_valid_team:
        leaderboard = _build_leaderboard(team_year)
        for entry in leaderboard:
            if entry["year"] == team_year:
                team_rank = entry["rank"]
                break

    # Contribution to team
    team_total_points = 0
    contribution_percent = 0
    if is_valid_team:
        team_total_points = (
            ChallengeSolve.objects.filter(team_year=team_year).aggregate(
                total=Sum("awarded_points")
            )["total"]
            or 0
        )
        if team_total_points > 0:
            contribution_percent = round((total_points / team_total_points) * 100, 1)

    # All submission attempts by this user, correct or not
    user_submissions = (
        ChallengeSubmission.objects.filter(participant=target_user)
        .select_related("challenge", "challenge__category")
        .order_by("-created_at")
    )

    context = {
        "participant": participant,
        "target_user": target_user,
        "user_solves": user_solves,
        "user_submissions": user_submissions,
        "total_points": total_points,
        "total_solves": total_solves,
        "total_submissions": user_submissions.count(),
        "first_bloods": first_bloods,
        "category_stats": category_stats,
        "type_stats": type_stats,
        "team_year": team_year,
        "is_valid_team": is_valid_team,
        "team_rank": team_rank,
        "team_total_points": team_total_points,
        "contribution_percent": contribution_percent,
    }

    return render(request, "core/user_detail.html", context)


def challenge_detail_view(request, challenge_slug: str):
    """Display detailed analytics about a specific challenge."""
    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to access this page.")
        return redirect("core:challenge")

    challenge = get_object_or_404(
        Challenge.objects.select_related("category").prefetch_related("prerequisites"),
        slug=challenge_slug,
    )

    # Get all solves for this challenge
    solves = (
        ChallengeSolve.objects.filter(challenge=challenge)
        .select_related("participant")
        .order_by("created_at")
    )

    # First blood info
    first_blood = solves.first()

    # Stats by team year
    team_stats_raw = (
        solves.values("team_year")
        .annotate(
            solve_count=Count("id"),
            points=Sum("awarded_points"),
        )
        .order_by("team_year")
    )

    # Time to first solve per team
    team_solve_times = {}
    for solve in solves:
        if solve.team_year not in team_solve_times:
            team_solve_times[solve.team_year] = solve.created_at.astimezone(
                settings.SCAV_HUNT_TZ
            )

    # Combine team stats with first solve times
    team_stats = []
    for stat in team_stats_raw:
        team_stats.append(
            {
                "team_year": stat["team_year"],
                "solve_count": stat["solve_count"],
                "points": stat["points"],
                "first_solve_time": team_solve_times.get(stat["team_year"]),
            }
        )

    # Challenges that depend on this one
    dependent_challenges = Challenge.objects.filter(
        prerequisites=challenge, is_active=True
    ).select_related("category")

    # Prerequisites for this challenge
    prerequisites = challenge.prerequisites.all()

    # Points progression (for decreasing challenges)
    points_progression = []
    if challenge.is_decreasing():
        for i in range(len(solves) + 3):  # Show a few potential future values
            points_progression.append(
                {
                    "solve_number": i + 1,
                    "points": challenge.points_for_next_solve(i),
                }
            )

    # All submission attempts for this challenge, correct or not
    submissions = (
        ChallengeSubmission.objects.filter(challenge=challenge)
        .select_related("participant")
        .order_by("-created_at")
    )

    context = {
        "participant": participant,
        "challenge": challenge,
        "solves": solves,
        "submissions": submissions,
        "first_blood": first_blood,
        "team_stats": team_stats,
        "dependent_challenges": dependent_challenges,
        "prerequisites": prerequisites,
        "points_progression": points_progression,
        "total_solves": solves.count(),
        "total_submissions": submissions.count(),
        "current_points": challenge.points_for_next_solve(solves.count()),
    }

    return render(request, "core/challenge_detail.html", context)


def links_view(request):
    """Create and manage short links, for scavcomm and admins."""

    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to access this page.")
        return redirect("core:challenge")

    if request.method == "POST":
        target_url = (request.POST.get("target_url") or "").strip()
        alias = (request.POST.get("alias") or "").strip().lower()

        if not target_url or not alias:
            messages.error(request, "Both a destination URL and an alias are required.")
        elif not ALIAS_PATTERN.match(alias):
            messages.error(
                request,
                "Alias may only contain lowercase letters, numbers, and hyphens, "
                "and can't start or end with a hyphen.",
            )
        else:
            try:
                URLValidator(schemes=["http", "https"])(target_url)
            except ValidationError:
                messages.error(request, "Enter a valid http:// or https:// URL to shorten.")
            else:
                try:
                    ShortLink.objects.create(
                        alias=alias, target_url=target_url, created_by=participant
                    )
                except IntegrityError:
                    messages.error(request, f'The alias "{alias}" is already in use.')
                else:
                    messages.success(request, f"Short link created: /s/{alias}/")

        return redirect("core:links")

    links = ShortLink.objects.select_related("created_by").order_by("-created_at")

    context = {
        "participant": participant,
        "links": links,
        "link_prefix": request.build_absolute_uri("/s/"),
    }
    return render(request, "core/links.html", context)


@require_POST
def revoke_link_view(request, link_id: int):
    """Revoke a short link so it stops resolving."""

    participant = _get_logged_in_participant(request)
    if not participant:
        return redirect("core:login")

    if not participant.can_view_analytics():
        messages.error(request, "You do not have permission to perform this action.")
        return redirect("core:challenge")

    link = get_object_or_404(ShortLink, pk=link_id)
    link.is_active = False
    link.save(update_fields=["is_active"])
    messages.success(request, f"Revoked /s/{link.alias}/.")

    return redirect("core:links")


def short_link_redirect_view(request, alias: str):
    """Resolve a short link alias and redirect to its target."""

    participant = _get_logged_in_participant(request)
    if not participant:
        request.session[POST_LOGIN_REDIRECT_SESSION_KEY] = request.get_full_path()
        return redirect("core:login")

    link = get_object_or_404(ShortLink, alias=alias, is_active=True)

    ShortLink.objects.filter(pk=link.pk).update(
        click_count=F("click_count") + 1, last_accessed_at=timezone.now()
    )

    return redirect(link.target_url)
