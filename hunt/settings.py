"""Project settings for the Scavenger Hunt site."""

from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import environ
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / "subdir".
BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    ION_SCOPE=(list, ["read"]),
    SCAV_HUNT_TEAM_YEARS=(list, []),
    SCAV_SUBMISSION_COOLDOWN_SECONDS=(int, 3),
    SCAV_LINK_RATE_LIMIT_SECONDS=(int, 4),
    HUNT_YEAR=(str, "2025"),  # Changed to str to accept any string value
    ENABLE_SNOW_OVERLAY=(bool, True),
)

# If a .env file exists, load it before reading any env vars so defaults
# declared above (like HUNT_YEAR) are overridden by file values.
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

# Make HUNT_YEAR available as a module-level setting
HUNT_YEAR = env("HUNT_YEAR")
ENABLE_SNOW_OVERLAY = env("ENABLE_SNOW_OVERLAY")

# Convert to int if it's a numeric string, otherwise keep as string
try:
    HUNT_YEAR = int(HUNT_YEAR)
except (ValueError, TypeError):
    pass  # Keep as string if not convertible to int

SECRET_KEY = env("DJANGO_SECRET_KEY", default=None)
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set via environment variable or .env file."
    )

DEBUG = env("DJANGO_DEBUG")

if DEBUG:
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS", default=["http://localhost", "https://localhost"]
)


SCAV_HUNT_TZ = ZoneInfo("America/New_York")


def _read_hunt_datetime(setting_name: str) -> datetime | None:
    value = env(setting_name, default=None)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - configuration error path
        raise ImproperlyConfigured(
            f"{setting_name} must be an ISO 8601 datetime (e.g. 2026-03-15T08:00:00)."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SCAV_HUNT_TZ)
    else:
        parsed = parsed.astimezone(SCAV_HUNT_TZ)
    return parsed


SCAV_HUNT_START = _read_hunt_datetime("SCAV_HUNT_START")
SCAV_HUNT_END = _read_hunt_datetime("SCAV_HUNT_END")


def _build_team_years() -> list[int]:
    raw_years = env("SCAV_HUNT_TEAM_YEARS")
    team_years: list[int] = []
    for raw in raw_years:
        if not raw:
            continue
        try:
            team_years.append(int(raw))
        except ValueError as exc:  # pragma: no cover - configuration error path
            raise ImproperlyConfigured(
                "SCAV_HUNT_TEAM_YEARS must contain comma-separated integers."
            ) from exc

    if team_years and len(team_years) != 4:
        raise ImproperlyConfigured(
            "SCAV_HUNT_TEAM_YEARS must define exactly four graduation years."
        )

    if not team_years:
        reference_dt = SCAV_HUNT_END or datetime.now(tz=SCAV_HUNT_TZ)
        reference_year = reference_dt.year
        team_years = [
            reference_year - 3,
            reference_year - 2,
            reference_year - 1,
            reference_year,
        ]

    return sorted(team_years)


SCAV_HUNT_TEAM_YEARS = _build_team_years()


# Challenge submission rate limit (seconds between attempts)
SCAV_SUBMISSION_COOLDOWN_SECONDS = env("SCAV_SUBMISSION_COOLDOWN_SECONDS")

# Rate limit (seconds between attempts, per IP) applied to short-link redirects
# and any other unrecognized URL, to blunt brute-forcing of short link aliases.
SCAV_LINK_RATE_LIMIT_SECONDS = env("SCAV_LINK_RATE_LIMIT_SECONDS")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.ShortLinkRateLimitMiddleware",
]

ROOT_URLCONF = "hunt.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.hunt_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "hunt.wsgi.application"


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Ion OAuth settings (placeholders for future integration)
ION_AUTHORIZE_URL = env(
    "ION_AUTHORIZE_URL", default="https://ion.tjhsst.edu/oauth/authorize/"
)
ION_TOKEN_URL = env("ION_TOKEN_URL", default="https://ion.tjhsst.edu/oauth/token/")
ION_PROFILE_URL = env("ION_PROFILE_URL", default="https://ion.tjhsst.edu/api/profile")
ION_CLIENT_ID = env("ION_CLIENT_ID", default=None)
ION_CLIENT_SECRET = env("ION_CLIENT_SECRET", default=None)
ION_REDIRECT_URI = env("ION_REDIRECT_URI", default=None)
ION_SCOPE = env("ION_SCOPE")
