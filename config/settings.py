from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env when present. Container platforms inject env vars directly.
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:  # pragma: no cover
    pass


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [value.strip() for value in os.getenv(name, default).split(",") if value.strip()]


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
# Platform-provided hostnames (Render, Railway, Fly, Heroku, Vercel…).
for _platform_var in ("RENDER_EXTERNAL_HOSTNAME", "RAILWAY_PUBLIC_DOMAIN", "FLY_APP_NAME"):
    _platform_host = os.getenv(_platform_var, "").strip()
    if _platform_host:
        ALLOWED_HOSTS.append(_platform_host)
        CSRF_TRUSTED_ORIGINS.append(f"https://{_platform_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "apps.common",
    "apps.accounts",
    "apps.audit",
    "apps.jobs",
    "apps.candidates",
    "apps.applications",
    "apps.interviews",
    "apps.reports",
    "apps.tasks",
    "apps.web",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "apps.accounts.middleware.MFAEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.common.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "NAME": "jinja2",
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "templates_jinja2"],
        "APP_DIRS": True,
        "OPTIONS": {"environment": "config.jinja2.environment"},
    },
    {
        "NAME": "django",
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    },
]

database_url = os.getenv("DATABASE_URL", "").strip()

if not database_url:
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}
    }
else:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.split("+", 1)[0].lower()
    name = unquote(parsed.path.lstrip("/"))
    conn_max_age = int(os.getenv("DB_CONN_MAX_AGE", "0" if DEBUG else "600"))
    options: dict = {}
    if "sslmode" in parsed.query:
        for pair in parsed.query.split("&"):
            key, _, value = pair.partition("=")
            if key == "sslmode":
                options["sslmode"] = value

    if scheme in {"postgres", "postgresql", "psql"}:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": name or "postgres",
                "USER": parsed.username or "",
                "PASSWORD": parsed.password or "",
                "HOST": parsed.hostname or "",
                "PORT": parsed.port or 5432,
                "CONN_MAX_AGE": conn_max_age,
                # Neon and most managed pools require TLS; keep it explicit.
                "OPTIONS": {**{"sslmode": "require"}, **options},
            }
        }
    elif scheme in {"mysql", "mysql+pymysql"}:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.mysql",
                "NAME": name,
                "USER": parsed.username,
                "PASSWORD": parsed.password,
                "HOST": parsed.hostname,
                "PORT": parsed.port or 3306,
                "CONN_MAX_AGE": conn_max_age,
                "OPTIONS": {"charset": "utf8mb4"},
            }
        }
    elif scheme == "sqlite":
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": (BASE_DIR / name) if name and name != ":memory:" else (BASE_DIR / "db.sqlite3"),
            }
        }
    else:
        raise ImproperlyConfigured(
            f"Unsupported DATABASE_URL scheme '{parsed.scheme}'. "
            "Use postgres://, mysql:// or sqlite://."
        )

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("id", "Bahasa Indonesia")]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static_dist"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Manifest hashing needs a completed collectstatic run, so keep the plain
    # storage while developing to avoid "missing manifest entry" errors.
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

if env_bool("USE_S3"):
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}
    AWS_S3_ENDPOINT_URL = os.getenv("OBJECT_STORAGE_ENDPOINT")
    AWS_STORAGE_BUCKET_NAME = os.getenv("OBJECT_STORAGE_BUCKET", "ats-resumes")
    AWS_ACCESS_KEY_ID = os.getenv("OBJECT_STORAGE_ACCESS_KEY")
    AWS_SECRET_ACCESS_KEY = os.getenv("OBJECT_STORAGE_SECRET_KEY")
    AWS_S3_REGION_NAME = os.getenv("OBJECT_STORAGE_REGION", "us-east-1")
    AWS_QUERYSTRING_EXPIRE = 300

EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "1025"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "ATS <noreply@ats.local>")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)

# Shared AI gateway (OpenAI-compatible). Match scoring stays on the deterministic
# rules engine unless AI_NARRATIVE_SCORES is enabled; the credential lives only in
# the environment and is never written to a log line.
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai.autoapp.biz.id/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "60"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "2"))
AI_MODEL_DEFAULT = os.getenv("AI_MODEL_DEFAULT", "minimax-m3")
AI_MODEL_CHAT = os.getenv("AI_MODEL_CHAT", "")
AI_MODEL_ANALYZE = os.getenv("AI_MODEL_ANALYZE", "glm-5.3-flash")
AI_MODEL_GENERATE = os.getenv("AI_MODEL_GENERATE", "minimax-m3")
AI_MODEL_SUMMARIZE = os.getenv("AI_MODEL_SUMMARIZE", "glm-5.3-flash")
# Opt-in: adds an LLM-written summary next to the rules score. The hiring
# disclaimer is always kept, and the numeric score never changes.
AI_NARRATIVE_SCORES = env_bool("AI_NARRATIVE_SCORES", False)
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", DEBUG)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_ROUTES = {"apps.candidates.tasks.*": {"queue": "resume_parse"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {"public_apply": "10/hour"},
}
SPECTACULAR_SETTINGS = {"TITLE": "ATS Resume Parser API", "VERSION": "1.0.0"}

MAX_RESUME_MB = int(os.getenv("MAX_RESUME_MB", "10"))
# Django defaults to a 2.5 MB request body limit, which would reject a valid
# resume before our own validation could return a helpful message.
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_RESUME_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_RESUME_MB * 1024 * 1024
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "")
PII_HASH_KEY = os.getenv("PII_HASH_KEY", SECRET_KEY)
CLAMAV_HOST = os.getenv("CLAMAV_HOST", "")
CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
MALWARE_SCAN_REQUIRED = env_bool("MALWARE_SCAN_REQUIRED", False)
MFA_ENFORCEMENT = env_bool("MFA_ENFORCEMENT", False)
# Credentials advertised on the public landing page when the demo user exists.
DEMO_EMAIL = os.getenv("DEMO_EMAIL", "demo@example.com")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "demo123")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Managed platforms terminate TLS in front of gunicorn; without this the
    # SECURE_SSL_REDIRECT check can loop.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "apps.common.logging.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
