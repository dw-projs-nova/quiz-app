import os


basedir = os.path.abspath(os.path.dirname(__file__))


def env_bool(name, default=False):
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(basedir, "app.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SECRET_KEY = os.getenv("SECRET_KEY", "change-this-local-development-secret")
    DEBUG = env_bool("FLASK_DEBUG", False)

    SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "25"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_USE_SSL = env_bool("SMTP_USE_SSL", False)
    SMTP_STARTTLS = env_bool("SMTP_STARTTLS", False)
    SMTP_FROM = os.getenv("SMTP_FROM", "Quiz App <noreply@localhost>")

    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
    INSTRUCTOR_EMAIL = os.getenv("INSTRUCTOR_EMAIL", "").strip().lower()
