from flask import abort, render_template

from controllers.shared import main, permission_required


@main.route("/student/", endpoint="student_dashboard")
@permission_required("quiz.take")
def student_dashboard():
    abort(501, description="Student dashboard is not implemented.")


@main.route("/quizzes", endpoint="available_quizzes", methods=["GET"])
@permission_required("quiz.take")
def available_quizzes():
    abort(501, description="Published quiz listing is not implemented.")


@main.route("/quizzes/<int:quiz_id>", endpoint="quiz_details")
@permission_required("quiz.take")
def quiz_details(quiz_id):
    abort(501, description="Quiz details are not implemented.")


@main.route(
    "/quizzes/<int:quiz_id>/attempts",
    methods=["POST"],
    endpoint="start_quiz_attempt",
)
@permission_required("quiz.take")
def start_quiz_attempt(quiz_id):
    abort(501, description="Starting a quiz attempt is not implemented.")


@main.route("/attempts/<int:attempt_id>", endpoint="take_quiz_attempt")
@permission_required("quiz.take")
def take_quiz_attempt(attempt_id):
    abort(501, description="Taking or resuming a quiz attempt is not implemented.")


@main.route(
    "/attempts/<int:attempt_id>/answers",
    methods=["POST"],
    endpoint="save_quiz_answer",
)
@permission_required("quiz.take")
def save_quiz_answer(attempt_id):
    abort(501, description="Saving quiz answers is not implemented.")


@main.route(
    "/attempts/<int:attempt_id>/submit",
    methods=["POST"],
    endpoint="submit_quiz_attempt",
)
@permission_required("quiz.take")
def submit_quiz_attempt(attempt_id):
    abort(501, description="Submitting a quiz attempt is not implemented.")


@main.route(
    "/attempts/<int:attempt_id>/result",
    endpoint="quiz_attempt_result",
)
@permission_required("quiz.take")
def quiz_attempt_result(attempt_id):
    abort(501, description="Quiz attempt results are not implemented.")
