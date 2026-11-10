import unicodedata
from decimal import Decimal

from flask import abort

from models import Quiz, QuizAnswer, QuizAttempt, db


def normalize_short_answer(value):
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def auto_grade_attempt(attempt):
    quiz = db.session.get(Quiz, attempt.quiz_id)
    if quiz is None:
        abort(404)
    answers = {
        answer.question_id: answer
        for answer in QuizAnswer.query.filter_by(attempt_id=attempt.id).all()
    }
    needs_manual_review = False

    for question in quiz.questions:
        answer = answers.get(question.id)
        if answer is None:
            continue
        if question.question_type in {"single_choice", "true_false"}:
            answer.points_awarded = (
                question.points
                if any(
                    option.id == answer.selected_option_id and option.is_correct
                    for option in question.options
                )
                else Decimal(0)
            )
        elif question.question_type == "short_answer":
            accepted = {
                normalize_short_answer(item.answer)
                for item in question.accepted_answers
            }
            if not accepted:
                answer.points_awarded = None
                needs_manual_review = True
            else:
                answer.points_awarded = (
                    question.points
                    if normalize_short_answer(answer.response_text or "") in accepted
                    else Decimal(0)
                )

    if needs_manual_review:
        attempt.status = "submitted"
        attempt.score = None
    else:
        attempt.status = "graded"
        attempt.score = sum(
            (answer.points_awarded or Decimal(0) for answer in answers.values()),
            Decimal(0),
        )
    return not needs_manual_review
