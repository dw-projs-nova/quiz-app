from flask import abort, g, redirect, request, url_for
from flask_admin import Admin, AdminIndexView
from flask_admin.contrib.sqla import ModelView
from flask_admin.theme import Theme
from sqlalchemy import inspect as sqlalchemy_inspect
from werkzeug.exceptions import HTTPException
from wtforms.fields.choices import SelectChoice
from wtforms import BooleanField, PasswordField
from werkzeug.security import generate_password_hash

from controllers.shared import (
    ROLE_LABELS,
    ROLE_PERMISSIONS,
    has_permission,
)
from models import (
    ClassEnrollment,
    ClassGroup,
    QuestionOption,
    QuizAcceptedAnswer,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAllowance,
    QuizAttemptInvalidation,
    QuizAttemptLock,
    QuizAttemptPause,
    QuizAttemptTiming,
    QuizClassAssignment,
    QuizClosure,
    QuizExamMode,
    QuizQuestion,
    QuizQuestionSection,
    QuizSection,
    QuizSettings,
    QuizStudentAssignment,
    LoginToken,
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


class FlaskAdminChoice(SelectChoice):
    @property
    def name(self):
        return self.label


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


class QuizSettingsView(QuizAdminModelView):
    form_columns = (
        "quiz",
        "available_from",
        "available_until",
        "interface_theme",
        "new_password",
        "clear_password",
    )
    form_extra_fields = {
        "new_password": PasswordField(
            "Set quiz password",
            description="Leave blank to keep the current password.",
        ),
        "clear_password": BooleanField(
            "Remove quiz password",
            description="Select this to allow access without a password.",
        ),
    }
    column_list = (
        "quiz_id",
        "available_from",
        "available_until",
        "interface_theme",
    )
    form_choices = {
        "interface_theme": tuple(
            FlaskAdminChoice(value=value, label=label)
            for value, label in (
                ("classic", "Classic cards"),
                ("focus", "Focus mode"),
                ("compact", "Compact list"),
            )
        )
    }

    def on_model_change(self, form, model, is_created):
        if form.new_password.data:
            model.password_hash = generate_password_hash(form.new_password.data)
        elif form.clear_password.data:
            model.password_hash = None
        for field_name in ("new_password", "clear_password"):
            if hasattr(model, field_name):
                delattr(model, field_name)


class QuizExamModeView(QuizAdminModelView):
    form_columns = ("quiz", "enabled")
    column_list = ("quiz_id", "enabled")


class QuizSectionView(QuizAdminModelView):
    form_columns = ("quiz", "position", "title")
    column_list = ("id", "quiz_id", "position", "title")


class QuizQuestionSectionView(QuizAdminModelView):
    form_columns = ("question", "section")
    column_list = ("question_id", "section_id")


class QuizClosureView(QuizAdminModelView):
    form_columns = ("quiz", "ended_at")
    column_list = ("quiz_id", "ended_at")


class ClassGroupView(QuizAdminModelView):
    form_columns = ("instructor", "name", "class_code")
    column_list = ("id", "name", "class_code", "instructor_id", "created_at")
    column_searchable_list = ("name", "class_code")


class ClassEnrollmentView(QuizAdminModelView):
    form_columns = ("class_group", "student", "joined_at")
    column_list = ("id", "class_id", "student_id", "joined_at")


class QuizClassAssignmentView(QuizAdminModelView):
    form_columns = ("quiz", "class_group")
    column_list = ("id", "quiz_id", "class_id")


class QuizStudentAssignmentView(QuizAdminModelView):
    form_columns = ("quiz", "student")
    column_list = ("id", "quiz_id", "student_id")


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
        ),
        (
            QuizAcceptedAnswer,
            {
                "form_columns": ("id", "position", "answer"),
                "column_labels": {
                    "position": "Order",
                    "answer": "Accepted response",
                },
                "form_args": {
                    "position": {
                        "description": "Order of an accepted short-answer response.",
                    },
                    "answer": {
                        "label": "Accepted response",
                        "description": "Short answers matching this response are automatically graded correct.",
                    },
                },
            },
        ),
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
    form_choices = {
        "status": tuple(
            FlaskAdminChoice(value=value, label=label)
            for value, label in (
                ("in_progress", "In progress"),
                ("submitted", "Submitted"),
                ("graded", "Graded"),
            )
        )
    }


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


class QuizAttemptTimingView(QuizAdminModelView):
    form_columns = ("attempt", "deadline_at")
    column_list = ("attempt_id", "deadline_at")


class QuizAttemptPauseView(QuizAdminModelView):
    form_columns = ("attempt", "paused_at")
    column_list = ("attempt_id", "paused_at")


class QuizAttemptLockView(QuizAdminModelView):
    form_columns = ("attempt", "locked_at", "reason")
    column_list = ("attempt_id", "locked_at", "reason")
    form_choices = {
        "reason": tuple(
            FlaskAdminChoice(value=value, label=label)
            for value, label in (
                ("instructor", "Instructor"),
                ("focus_lost", "Locked after tab lost focus"),
                ("saved_progress", "Locked after saving progress"),
                ("quiz_ended", "Quiz ended"),
            )
        )
    }


class QuizAttemptAllowanceView(QuizAdminModelView):
    form_columns = ("quiz_id", "user_id", "additional_attempts")
    column_list = ("quiz_id", "user_id", "additional_attempts")


class QuizAttemptInvalidationView(QuizAdminModelView):
    form_columns = ("attempt_id", "invalidated_by_id", "invalidated_at")
    column_list = ("attempt_id", "invalidated_by_id", "invalidated_at")


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
        LoginToken.query.filter_by(user_id=model.id).delete(
            synchronize_session=False
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
        )
    )
    admin.add_view(
        QuizView(
            Quiz,
            db.session,
            name="Quizzes",
            endpoint="admin_quizzes_data",
            url="/admin/quiz",
            category="Quiz management",
        )
    )
    for view_class, model, name, endpoint, path in (
        (
            QuizSettingsView,
            QuizSettings,
            "Availability and theme",
            "admin_quiz_settings",
            "/admin/quiz-settings",
        ),
        (
            QuizExamModeView,
            QuizExamMode,
            "Exam mode",
            "admin_exam_modes",
            "/admin/exam-mode",
        ),
        (
            QuizSectionView,
            QuizSection,
            "Sections",
            "admin_quiz_sections",
            "/admin/quiz-section",
        ),
        (
            QuizQuestionSectionView,
            QuizQuestionSection,
            "Question sections",
            "admin_question_sections",
            "/admin/question-section",
        ),
        (
            QuizClosureView,
            QuizClosure,
            "Quiz closures",
            "admin_quiz_closures",
            "/admin/quiz-closure",
        ),
    ):
        admin.add_view(
            view_class(
                model,
                db.session,
                name=name,
                endpoint=endpoint,
                url=path,
                category="Quiz management",
            )
        )
    admin.add_view(
        QuizQuestionView(
            QuizQuestion,
            db.session,
            name="Questions",
            endpoint="admin_questions_data",
            url="/admin/question",
            category="Quiz management",
        )
    )
    admin.add_view(
        QuizAttemptView(
            QuizAttempt,
            db.session,
            name="Attempts",
            endpoint="admin_attempts_data",
            url="/admin/attempt",
            category="Attempts",
        )
    )
    admin.add_view(
        QuizAnswerView(
            QuizAnswer,
            db.session,
            name="Answers",
            endpoint="admin_answers_data",
            url="/admin/answer",
            category="Attempts",
        )
    )
    for view_class, model, name, endpoint, path, category in (
        (
            QuizAttemptTimingView,
            QuizAttemptTiming,
            "Attempt timers",
            "admin_attempt_timings",
            "/admin/attempt-timing",
            "Attempts",
        ),
        (
            QuizAttemptPauseView,
            QuizAttemptPause,
            "Paused attempts",
            "admin_attempt_pauses",
            "/admin/attempt-pause",
            "Attempts",
        ),
        (
            QuizAttemptLockView,
            QuizAttemptLock,
            "Attempt locks",
            "admin_attempt_locks",
            "/admin/attempt-lock",
            "Attempts",
        ),
        (
            QuizAttemptAllowanceView,
            QuizAttemptAllowance,
            "Extra attempt grants",
            "admin_attempt_allowances",
            "/admin/attempt-allowance",
            "Attempts",
        ),
        (
            QuizAttemptInvalidationView,
            QuizAttemptInvalidation,
            "Invalidated attempts",
            "admin_attempt_invalidations",
            "/admin/attempt-invalidation",
            "Attempts",
        ),
        (
            ClassGroupView,
            ClassGroup,
            "Classes",
            "admin_classes",
            "/admin/class",
            "Classes",
        ),
        (
            ClassEnrollmentView,
            ClassEnrollment,
            "Enrollments",
            "admin_class_enrollments",
            "/admin/class-enrollment",
            "Classes",
        ),
        (
            QuizClassAssignmentView,
            QuizClassAssignment,
            "Class quiz assignments",
            "admin_class_quiz_assignments",
            "/admin/class-quiz-assignment",
            "Classes",
        ),
        (
            QuizStudentAssignmentView,
            QuizStudentAssignment,
            "Individual quiz assignments",
            "admin_student_quiz_assignments",
            "/admin/student-quiz-assignment",
            "Classes",
        ),
    ):
        admin.add_view(
            view_class(
                model,
                db.session,
                name=name,
                endpoint=endpoint,
                url=path,
                category=category,
            )
        )
    return admin
