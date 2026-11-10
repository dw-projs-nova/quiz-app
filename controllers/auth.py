from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe
from smtplib import SMTPException
from urllib.parse import urlsplit

from flask import (
    abort,
    current_app,
    g,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError

from controllers.mail import send_login_email
from controllers.shared import main, require_login
from models import ClassEnrollment, ClassGroup, LoginToken, PendingLoginToken, User, db


def login_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="quiz-login")


def token_hash(token):
    return sha256(token.encode("utf-8")).hexdigest()


def safe_next_url(value):
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    parsed = urlsplit(value)
    return value if not parsed.netloc else None


def send_sign_in_link(email, user=None):
    nonce = token_urlsafe(32)
    token = login_serializer().dumps(
        {
            "email": email,
            "user_id": user.id if user is not None else None,
            "nonce": nonce,
        }
    )
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=30)
    db.session.add(
        PendingLoginToken(
            token_hash=token_hash(token),
            email=email,
            user_id=user.id if user is not None else None,
            expires_at=expires_at,
        )
    )
    db.session.commit()
    send_login_email(email, url_for("main.verify_login", token=token, _external=True))


@main.route("/login", methods=["GET", "POST"])
def login():
    if g.get("current_user") is not None:
        return redirect(url_for("main.account"))

    if request.method == "POST":
        next_url = safe_next_url(request.form.get("next"))
        if next_url:
            session["login_next"] = next_url

        email = request.form.get("email", "").strip().lower()
        if "@" not in email or len(email) > 320:
            return render_template(
                "account/auth/login.html",
                error="Enter a valid email address.",
            ), 400

        user = User.query.filter_by(email=email).first()
        try:
            send_sign_in_link(email, user)
        except (OSError, SMTPException) as exc:
            current_app.logger.exception("Could not send sign-in email")
            return render_template(
                "account/auth/login.html",
                error=f"Could not send email: {exc}",
            ), 502

        return render_template(
            "account/auth/login.html",
            message="Check your email for a secure sign-in link.",
        )

    return render_template("account/auth/login.html")


def load_login_token(token):
    try:
        data = login_serializer().loads(token, max_age=30 * 60)
        nonce = data["nonce"]
        record = db.session.get(PendingLoginToken, token_hash(token))
        if record is not None:
            email = data["email"]
            user_id = data["user_id"]
            if email != record.email or user_id != record.user_id:
                abort(400)
            user = db.session.get(User, user_id) if user_id is not None else None
            if user is not None and user.email != email:
                abort(400)
            pending = True
        else:
            user_id = data["user_id"]
            record = db.session.get(LoginToken, token_hash(token))
            user = db.session.get(User, user_id)
            pending = False
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        abort(400)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        not nonce
        or record is None
        or record.used_at is not None
        or record.expires_at <= now
    ):
        abort(400)
    if not pending and (record.user_id != user_id or user is None):
        abort(400)
    return record, user, pending


def create_or_get_user(email):
    user = User.query.filter_by(email=email).first()
    if user is not None:
        return user

    role = "admin" if email == current_app.config["ADMIN_EMAIL"] else "instructor" if email == current_app.config["INSTRUCTOR_EMAIL"] else "student"
    try:
        with db.session.begin_nested():
            user = User(
                email=email,
                display_name=email.split("@", 1)[0],
                role=role,
            )
            db.session.add(user)
            db.session.flush()
    except IntegrityError:
        user = User.query.filter_by(email=email).first()
        if user is None:
            raise
    return user


@main.route("/login/verify/<token>", methods=["GET", "POST"])
def verify_login(token):
    record, user, pending = load_login_token(token)
    if request.method == "GET":
        response = make_response(render_template("account/auth/verify_login.html"))
        response.headers["Cache-Control"] = "no-store"
        return response

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    token_model = PendingLoginToken if pending else LoginToken
    consumed = token_model.query.filter(
        token_model.token_hash == record.token_hash,
        token_model.used_at.is_(None),
        token_model.expires_at > now,
    ).update({token_model.used_at: now}, synchronize_session=False)
    if consumed != 1:
        db.session.rollback()
        abort(400)

    if pending:
        user = (
            db.session.get(User, record.user_id)
            if record.user_id is not None
            else None
        )
        if user is None:
            user = create_or_get_user(record.email)
        if record.email == current_app.config["ADMIN_EMAIL"]:
            user.role = "admin"

    next_url = safe_next_url(session.pop("login_next", None))
    session.clear()
    session["user_id"] = user.id
    db.session.commit()
    return redirect(next_url or url_for("main.account"))


@main.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@main.route("/account", methods=["GET", "POST"])
def account():
    login_redirect = require_login()
    if login_redirect:
        return login_redirect

    joined_classes = []
    managed_class_count = 0
    if g.current_user.role == "student":
        joined_classes = (
            ClassEnrollment.query.filter_by(student_id=g.current_user.id)
            .order_by(ClassEnrollment.joined_at.desc())
            .all()
        )
    elif g.current_user.role == "instructor":
        managed_class_count = ClassGroup.query.filter_by(
            instructor_id=g.current_user.id
        ).count()

    message = None
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        if not display_name or len(display_name) > 120:
            return render_template(
                "account/profile.html",
                error="Display name is required and must be 120 characters or fewer.",
                joined_classes=joined_classes,
                managed_class_count=managed_class_count,
            ), 400
        g.current_user.display_name = display_name
        db.session.commit()
        message = "Profile updated."

    return render_template(
        "account/profile.html",
        message=message,
        joined_classes=joined_classes,
        managed_class_count=managed_class_count,
    )
