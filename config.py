import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env from the project root (only takes effect locally; no-op in production
# if variables are already set in the environment)
load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))


def _require_env(key: str) -> str:
    """Return the value of a required environment variable, raising if absent."""
    value = os.environ.get(key)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file (development) or server environment (production)."
        )
    return value


class Config:
    # ── Security ────────────────────────────────────────────────────────────
    # SECRET_KEY must be set externally — no insecure fallback.
    SECRET_KEY = _require_env('SECRET_KEY')

    # ── Database ────────────────────────────────────────────────────────────
    # DATABASE_URL must be set externally — no hardcoded credentials.
    SQLALCHEMY_DATABASE_URI = _require_env('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False

    # ── File uploads ────────────────────────────────────────────────────────
    UPLOAD_FOLDER = os.path.join(basedir, 'app/static/uploads')
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024   # 16 MB
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

    # ── Session / cookies ───────────────────────────────────────────────────
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    # Secure by default — subclasses must explicitly opt out for local dev.
    SESSION_COOKIE_SECURE   = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # ── Mail ────────────────────────────────────────────────────────────────
    MAIL_SERVER          = os.environ.get('MAIL_SERVER')
    MAIL_PORT            = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS         = os.environ.get('MAIL_USE_TLS', 'true').lower() in ('true', 'on', '1')
    MAIL_USERNAME        = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD        = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER  = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@janitorialqc.local')

    # ── Application base URL (used in email "View Details" links) ───────────
    # Set this to your production domain, e.g. https://qc.yourcompany.com
    APP_BASE_URL = os.environ.get('APP_BASE_URL', '')


class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_ECHO = True
    # Allow HTTP cookies during local development (HTTP, not HTTPS)
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    # Inherits SESSION_COOKIE_SECURE = True from Config — no override needed.


config = {
    'development': DevelopmentConfig,
    'production':  ProductionConfig,
    'default':     DevelopmentConfig,
}