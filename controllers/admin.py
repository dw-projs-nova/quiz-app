from flask import abort, g, redirect, render_template, request, url_for
from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Theme
from sqlalchemy import inspect as sqlalchemy_inspect
from werkzeug.exceptions import HTTPException
from wtforms.fields.choices import SelectChoice

from controllers.shared import (
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    has_permission,
    main,
    permission_required,
)
from models import (
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    User,
    db,
)


class QuizAdminAccessMixin:
    def is_accessible(self):
        return has_permission("quiz.manage_all")

    def inaccessible_callback(self, name, **kwargs):
        if g.get("current_user") is None:
            return redirect(url_for("main.login", next=request.full_path))
        abort(403)


class QuizAdminIndexView(QuizAdminAccessMixin, AdminIndexView):
    pass


class QuizAdminModelView(QuizAdminAccessMixin, ModelView):
    pass


class QuizView(QuizAdminModelView):
    form_columns = (
        "instructor_id",
        "title",
        "description",
        "is_published",
        "time_limit_seconds",
        "max_attempts",
    )
    column_list = ("id", "title", "instructor_id", "is_published")


class FlaskAdminChoice(SelectChoice):
    @property
    def name(self):
        return self.label


QUESTION_TYPE_LABELS = {
    "single_choice": "Multiple choice (one correct answer)",
    "true_false": "True or false",
    "short_answer": "Short answer",
}


class QuizQuestionView(QuizAdminModelView):
    form_columns = ("quiz", "position", "prompt", "question_type", "points")
    column_list = ("id", "quiz", "position", "prompt", "question_type", "points")
    column_labels = {
        "quiz": "Quiz",
        "position": "Order",
        "prompt": "Question",
        "question_type": "Answer type",
        "points": "Points",
    }
    column_formatters = {
        "quiz": lambda view, context, model, name: model.quiz.title,
        "question_type": lambda view, context, model, name: QUESTION_TYPE_LABELS.get(
            model.question_type,
            model.question_type,
        ),
    }
    form_choices = {
        "question_type": tuple(
            FlaskAdminChoice(value=value, label=label)
            for value, label in QUESTION_TYPE_LABELS.items()
        )
    }
    form_args = {
        "question_type": {
            "description": "Choose how a student will answer this question."
        },
        "position": {"description": "Display order within the quiz."},
    }
    inline_models = [
        (
            QuestionOption,
            {
                "form_columns": ("id", "position", "text", "is_correct"),
                "column_labels": {
                    "position": "Order",
                    "text": "Option",
                    "is_correct": "Correct answer",
                },
                "form_args": {
                    "position": {
                        "description": "Order this answer choice appears, starting at 1."
                    },
                    "text": {
                        "label": "Answer choice text",
                        "description": "The answer the student will see. Add one option for each possible choice.",
                    },
                    "is_correct": {
                        "label": "This is the correct answer",
                        "description": "Check the one choice that is correct for this question.",
                    },
                },
            },
        )
    ]


class QuizAttemptView(QuizAdminModelView):
    form_columns = (
        "quiz_id",
        "user_id",
        "attempt_number",
        "status",
        "started_at",
        "submitted_at",
        "score",
    )
    column_list = (
        "id",
        "quiz_id",
        "user_id",
        "attempt_number",
        "status",
        "score",
    )


class QuizAnswerView(QuizAdminModelView):
    form_columns = (
        "attempt_id",
        "quiz_id",
        "question_id",
        "selected_option_id",
        "response_text",
        "points_awarded",
    )
    column_list = (
        "id",
        "attempt_id",
        "question_id",
        "selected_option_id",
        "response_text",
        "points_awarded",
    )


class UserAdminAccessMixin:
    def is_accessible(self):
        return has_permission("users.manage")

    def inaccessible_callback(self, name, **kwargs):
        if g.get("current_user") is None:
            return redirect(url_for("main.login", next=request.full_path))
        abort(403)


class UserAdminView(UserAdminAccessMixin, ModelView):
    column_list = ("id", "email", "display_name", "role", "created_at")
    column_searchable_list = ("email", "display_name")
    column_filters = ("role",)
    form_columns = ("email", "display_name", "role")
    form_choices = {
        "role": tuple(
            FlaskAdminChoice(value=role, label=label)
            for role, label in ROLE_LABELS.items()
        )
    }
    can_view_details = True

    def handle_view_exception(self, exc):
        if isinstance(exc, HTTPException):
            raise exc
        return super().handle_view_exception(exc)

    def on_model_change(self, form, model, is_created):
        if is_created:
            if (
                model.role != "student"
                and "roles.assign" not in ROLE_PERMISSIONS.get(
                    g.current_user.role,
                    (),
                )
            ):
                abort(403)
            return

        role_history = sqlalchemy_inspect(model).attrs.role.history
        previous_role = role_history.deleted[0] if role_history.deleted else model.role
        if model.role == previous_role:
            return
        actor = g.get("current_user")
        actor_role = (
            previous_role
            if actor is model and role_history.deleted
            else actor.role if actor is not None else None
        )
        if "roles.assign" not in ROLE_PERMISSIONS.get(actor_role, ()):
            abort(403)
        if previous_role == "admin" and model.role != "admin":
            remaining_administrators = User.query.filter(
                User.role == "admin",
                User.id != model.id,
            ).count()
            if remaining_administrators == 0:
                abort(
                    409,
                    description="At least one administrator account must remain.",
                )

    def on_model_delete(self, model):
        if model.role == "admin":
            administrator_count = User.query.filter_by(role="admin").count()
            if administrator_count <= 1:
                abort(
                    409,
                    description="At least one administrator account must remain.",
                )


def init_admin(app):
    admin = Admin(
        app,
        name="Quiz App Admin",
        url="/admin",
        index_view=QuizAdminIndexView(endpoint="quiz_admin", url="/admin"),
        endpoint="quiz_admin",
        theme=Theme(
            folder="bootstrap4",
            base_template="admin/halfmoon_base.html",
        ),
    )
    admin.add_view(
        UserAdminView(
            User,
            db.session,
            name="Users",
            endpoint="admin_users_data",
            url="/admin/user",
            category="User management",
        )
    )
    admin.add_view(
        QuizView(
            Quiz,
            db.session,
            name="Quizzes",
            endpoint="admin_quizzes_data",
            url="/admin/quiz",
        )
    )
    admin.add_view(
        QuizQuestionView(
            QuizQuestion,
            db.session,
            name="Questions",
            endpoint="admin_questions_data",
            url="/admin/question",
        )
    )
    admin.add_view(
        QuizAttemptView(
            QuizAttempt,
            db.session,
            name="Attempts",
            endpoint="admin_attempts_data",
            url="/admin/attempt",
        )
    )
    admin.add_view(
        QuizAnswerView(
            QuizAnswer,
            db.session,
            name="Answers",
            endpoint="admin_answers_data",
            url="/admin/answer",
        )
    )
    return admin


@main.route("/admin/users", methods=["GET", "POST"])
@permission_required("users.manage")
def admin_users():
    message = None
    if request.method == "POST":
        if not has_permission("roles.assign"):
            abort(403)

        user_id = request.form.get("user_id", type=int)
        role = request.form.get("role")
        user = db.session.get(User, user_id) if user_id else None
        if user is None or role not in ROLE_LABELS:
            abort(400)

        if user.role == "admin" and role != "admin":
            administrator_count = User.query.filter_by(role="admin").count()
            if administrator_count <= 1:
                abort(409, description="At least one administrator account must remain.")

        user.role = role
        db.session.commit()
        message = f"{user.display_name}'s role was updated to {ROLE_LABELS[role]}."

    users = User.query.order_by(User.created_at.desc(), User.email.asc()).all()
    return render_template("admin/users.html", users=users, message=message)


@main.route("/admin/quizzes", endpoint="admin_quizzes")
@permission_required("quiz.manage_all")
def admin_quizzes():
    abort(501, description="Administrator quiz listing is not implemented.")


@main.route("/admin/dashboard", endpoint="admin_dashboard")
@permission_required("quiz.manage_all")
def admin_dashboard():
    return redirect(url_for("quiz_admin.index"))


@main.route(
    "/admin/quizzes/<int:quiz_id>",
    endpoint="admin_quiz_details",
)
@permission_required("quiz.manage_all")
def admin_quiz_details(quiz_id):
    abort(501, description="Administrator quiz details are not implemented.")


@main.route(
    "/admin/attempts/<int:attempt_id>",
    endpoint="admin_quiz_attempt_details",
)
@permission_required("quiz.manage_all")
def admin_quiz_attempt_details(attempt_id):
    abort(501, description="Administrator attempt review is not implemented.")
