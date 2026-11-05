from flask import abort, render_template

from controllers.shared import main, permission_required

@main.route("/testing/quiz", endpoint="available_quizzes_testing", methods=["GET"])
@permission_required("quiz.take")
def available_quizzes():
    return render_template("quiz.html")