from functools import wraps
from hmac import compare_digest
from secrets import token_urlsafe

from flask import Blueprint, abort, g, redirect, request, session, url_for

from models import User, db


main = Blueprint("main", __name__)

ROLE_PERMISSIONS = {
    "student": frozenset({"quiz.take"}),
    "instructor": frozenset({"quiz.take", "quiz.create", "quiz.manage_own"}),
    "admin": frozenset(
        {
            "quiz.take",
            "quiz.create",
            "quiz.manage_own",
            "quiz.manage_all",
            "users.manage",
            "roles.assign",
        }
    ),
}

ROLE_LABELS = {
    "student": "Student",
    "instructor": "Instructor",
    "admin": "Administrator",
}


def has_permission(permission, user=None):
    user = user if user is not None else g.get("current_user")
    return user is not None and permission in ROLE_PERMISSIONS.get(user.role, ())


@main.before_app_request
def load_current_user():
    user_id = session.get("user_id")
    g.current_user = db.session.get(User, user_id) if user_id is not None else None
    if user_id is not None and g.current_user is None:
        session.pop("user_id", None)


@main.before_app_request
def protect_state_changes():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get("csrf_token")
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not expected or not supplied or not compare_digest(expected, supplied):
        abort(400)


@main.app_context_processor
def add_template_user():
    def csrf_token():
        token = session.get("csrf_token")
        if token is None:
            token = token_urlsafe(32)
            session["csrf_token"] = token
        return token

    user = g.get("current_user")
    return {
        "current_user": user,
        "current_user_permissions": sorted(
            ROLE_PERMISSIONS.get(user.role, ()) if user is not None else ()
        ),
        "has_permission": has_permission,
        "role_labels": ROLE_LABELS,
        "csrf_token": csrf_token,
    }


def require_login():
    if g.get("current_user") is None:
        return redirect(url_for("main.login", next=request.full_path))
    return None


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            login_redirect = require_login()
            if login_redirect:
                return login_redirect
            if not has_permission(permission):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator
