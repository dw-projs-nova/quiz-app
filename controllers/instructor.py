from decimal import Decimal, InvalidOperation

from flask import abort, g, redirect, render_template, request, url_for
from sqlalchemy import func

from controllers.shared import has_permission, main, permission_required
from models import QuestionOption, Quiz, QuizQuestion, db


QUESTION_TYPES = {
    "single_choice": "Multiple choice",
    "true_false": "True or false",
    "short_answer": "Short answer",
}


def managed_quiz_or_404(quiz_id):
    query = Quiz.query.filter_by(id=quiz_id)
    if not has_permission("quiz.manage_all"):
        query = query.filter_by(instructor_id=g.current_user.id)
    return query.first_or_404()


@main.route("/instructor/", endpoint="instructor_dashboard")
@permission_required("quiz.create")
def instructor_dashboard():
    return redirect(url_for("main.instructor_quizzes"))


@main.route("/instructor/quizzes", endpoint="instructor_quizzes")
@permission_required("quiz.manage_own")
def instructor_quizzes():
    query = Quiz.query
    if not has_permission("quiz.manage_all"):
        query = query.filter_by(instructor_id=g.current_user.id)
    quizzes = query.order_by(Quiz.updated_at.desc(), Quiz.id.desc()).all()
    question_counts = dict(
        db.session.query(QuizQuestion.quiz_id, func.count(QuizQuestion.id))
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .filter(Quiz.id.in_([quiz.id for quiz in quizzes]))
        .group_by(QuizQuestion.quiz_id)
        .all()
    ) if quizzes else {}
    return render_template(
        "instructor/quizzes.html",
        quizzes=quizzes,
        question_counts=question_counts,
    )


@main.route(
    "/instructor/quizzes/new",
    methods=["GET", "POST"],
    endpoint="create_quiz",
)
@permission_required("quiz.create")
def create_quiz():
    values = {"title": "", "description": ""}
    error = None
    if request.method == "POST":
        values = {
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
        }
        if not values["title"] or len(values["title"]) > 200:
            error = "Enter a title of 1 to 200 characters."
        elif len(values["description"]) > 10000:
            error = "The description must be 10,000 characters or fewer."
        else:
            quiz = Quiz(
                instructor_id=g.current_user.id,
                title=values["title"],
                description=values["description"] or None,
            )
            db.session.add(quiz)
            db.session.commit()
            return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/quiz_form.html",
        quiz=None,
        values=values,
        error=error,
    ), 400 if error else 200


@main.route(
    "/instructor/quizzes/<int:quiz_id>",
    endpoint="instructor_quiz_details",
)
@permission_required("quiz.manage_own")
def instructor_quiz_details(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    question_values = {
        "prompt": "",
        "question_type": "single_choice",
        "points": "1",
        "options": "",
        "correct_option": "1",
    }
    return render_template(
        "instructor/quiz_detail.html",
        quiz=quiz,
        question_types=QUESTION_TYPES,
        question_values=question_values,
        error=None,
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/edit",
    methods=["GET", "POST"],
    endpoint="edit_quiz",
)
@permission_required("quiz.manage_own")
def edit_quiz(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        if not title or len(title) > 200:
            error = "Enter a title of 1 to 200 characters."
        elif len(description) > 10000:
            error = "The description must be 10,000 characters or fewer."
        else:
            quiz.title = title
            quiz.description = description or None
            db.session.commit()
            return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/quiz_form.html",
        quiz=quiz,
        values={
            "title": request.form.get("title", quiz.title),
            "description": request.form.get("description", quiz.description or ""),
        },
        error=error,
    ), 400 if error else 200


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/new",
    methods=["POST"],
    endpoint="create_quiz_question",
)
@permission_required("quiz.manage_own")
def create_quiz_question(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    values = {
        "prompt": request.form.get("prompt", "").strip(),
        "question_type": request.form.get("question_type", "single_choice"),
        "points": request.form.get("points", "1").strip(),
        "options": request.form.get("options", ""),
        "correct_option": request.form.get(
            "true_false_answer"
            if request.form.get("question_type") == "true_false"
            else "correct_option",
            "1",
        ).strip(),
    }
    error = None

    if not values["prompt"] or len(values["prompt"]) > 5000:
        error = "Enter a question of 1 to 5,000 characters."
    elif values["question_type"] not in QUESTION_TYPES:
        error = "Choose one of the listed answer types."
    try:
        points = Decimal(values["points"])
        if not points.is_finite() or points <= 0:
            error = "Points must be greater than zero."
    except InvalidOperation:
        points = Decimal(0)
        error = "Enter a valid positive points value."

    options = []
    correct_option = 0
    if values["question_type"] == "single_choice":
        options = [line.strip() for line in values["options"].splitlines() if line.strip()]
        try:
            correct_option = int(values["correct_option"])
        except ValueError:
            error = "Choose the number of the correct option."
        if len(options) < 2:
            error = "Multiple-choice questions need at least two options."
        elif not 1 <= correct_option <= len(options):
            error = "The correct option number must match an option in the list."
    elif values["question_type"] == "true_false":
        options = ["True", "False"]
        try:
            correct_option = int(values["correct_option"])
        except ValueError:
            error = "Choose whether the correct answer is True or False."
        if correct_option not in (1, 2):
            error = "Choose True or False as the correct answer."

    if error is None:
        last_position = (
            db.session.query(func.max(QuizQuestion.position))
            .filter_by(quiz_id=quiz.id)
            .scalar()
            or 0
        )
        question = QuizQuestion(
            quiz_id=quiz.id,
            position=last_position + 1,
            prompt=values["prompt"],
            question_type=values["question_type"],
            points=points,
        )
        for position, text in enumerate(options, start=1):
            question.options.append(
                QuestionOption(
                    position=position,
                    text=text,
                    is_correct=position == correct_option,
                )
            )
        db.session.add(question)
        db.session.commit()
        return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/quiz_detail.html",
        quiz=quiz,
        question_types=QUESTION_TYPES,
        question_values=values,
        error=error,
    ), 400


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/<int:question_id>/edit",
    methods=["GET", "POST"],
    endpoint="edit_quiz_question",
)
@permission_required("quiz.manage_own")
def edit_quiz_question(quiz_id, question_id):
    abort(501, description="Editing quiz questions is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/<int:question_id>/delete",
    methods=["POST"],
    endpoint="delete_quiz_question",
)
@permission_required("quiz.manage_own")
def delete_quiz_question(quiz_id, question_id):
    abort(501, description="Deleting quiz questions is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/publish",
    methods=["POST"],
    endpoint="publish_quiz",
)
@permission_required("quiz.manage_own")
def publish_quiz(quiz_id):
    abort(501, description="Publishing a quiz is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/delete",
    methods=["POST"],
    endpoint="delete_quiz",
)
@permission_required("quiz.manage_own")
def delete_quiz(quiz_id):
    abort(501, description="Deleting a quiz is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts",
    endpoint="quiz_attempts",
)
@permission_required("quiz.manage_own")
def quiz_attempts(quiz_id):
    abort(501, description="Viewing quiz attempts is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/report",
    endpoint="quiz_report",
)
@permission_required("quiz.manage_own")
def quiz_report(quiz_id):
    abort(501, description="Viewing quiz reports is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>",
    endpoint="review_quiz_attempt",
)
@permission_required("quiz.manage_own")
def review_quiz_attempt(quiz_id, attempt_id):
    abort(501, description="Reviewing quiz attempts is not implemented.")


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/grade",
    methods=["POST"],
    endpoint="grade_quiz_attempt",
)
@permission_required("quiz.manage_own")
def grade_quiz_attempt(quiz_id, attempt_id):
    abort(501, description="Grading quiz attempts is not implemented.")
