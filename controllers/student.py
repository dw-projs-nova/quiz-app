from datetime import datetime, timedelta, timezone
from math import ceil

from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy import func, or_, select
from werkzeug.security import check_password_hash

from controllers.quiz_grading import auto_grade_attempt
from controllers.shared import main, permission_required
from models import (
    ClassEnrollment,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAllowance,
    QuizAttemptInvalidation,
    QuizAttemptLock,
    QuizAttemptPause,
    QuizClassAssignment,
    QuizClosure,
    QuizQuestion,
    QuizAttemptTiming,
    QuizSettings,
    QuizStudentAssignment,
    db,
)


def published_quiz_or_404(quiz_id):
    quiz = assigned_quizzes_query().filter(
        Quiz.id == quiz_id,
        Quiz.is_published.is_(True),
    ).first_or_404()
    return quiz


def assigned_quizzes_query():
    class_quiz_ids = (
        select(QuizClassAssignment.quiz_id)
        .join(
            ClassEnrollment,
            ClassEnrollment.class_id == QuizClassAssignment.class_id,
        )
        .where(ClassEnrollment.student_id == g.current_user.id)
    )
    individual_quiz_ids = (
        select(QuizStudentAssignment.quiz_id).where(
            QuizStudentAssignment.student_id == g.current_user.id
        )
    )
    return Quiz.query.filter(
        or_(
            Quiz.id.in_(class_quiz_ids),
            Quiz.id.in_(individual_quiz_ids),
        )
    ).distinct()


def normalize_datetime(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def availability_status(quiz, now=None):
    if db.session.get(QuizClosure, quiz.id) is not None:
        return False, "This quiz has been ended by the instructor."
    settings = db.session.get(QuizSettings, quiz.id)
    now = now or datetime.now(timezone.utc)
    available_from = normalize_datetime(
        settings.available_from if settings else None
    )
    available_until = normalize_datetime(
        settings.available_until if settings else None
    )
    if available_from and now < available_from:
        return False, "This quiz opens " + available_from.astimezone(
            timezone.utc
        ).strftime("%b %d, %Y at %H:%M UTC.")
    if available_until and now >= available_until:
        return False, "The availability window for this quiz has closed."
    return True, None


def owned_attempt_or_404(attempt_id):
    return QuizAttempt.query.filter_by(
        id=attempt_id,
        user_id=g.current_user.id,
    ).filter(
        ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id))
    ).first_or_404()


def valid_attempt_count(quiz_id, user_id):
    return QuizAttempt.query.filter_by(
        quiz_id=quiz_id,
        user_id=user_id,
    ).filter(
        ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id))
    ).count()


def allowed_attempt_count(quiz, user_id):
    if quiz.max_attempts is None:
        return None
    allowance = db.session.get(QuizAttemptAllowance, (quiz.id, user_id))
    return quiz.max_attempts + (
        allowance.additional_attempts if allowance else 0
    )


def quiz_for_attempt_or_404(attempt):
    quiz = db.session.get(Quiz, attempt.quiz_id)
    if quiz is None:
        abort(404)
    return quiz


def attempt_deadline(attempt, quiz):
    if attempt.timing is not None:
        return normalize_datetime(attempt.timing.deadline_at)
    if quiz.time_limit_seconds is None:
        return None
    started_at = normalize_datetime(attempt.started_at)
    return started_at + timedelta(seconds=quiz.time_limit_seconds)


def attempt_lock_reason(attempt, quiz):
    if db.session.get(QuizClosure, quiz.id) is not None:
        return "This quiz has been ended by the instructor. Your saved answers were not submitted."
    lock = db.session.get(QuizAttemptLock, attempt.id)
    if lock is not None:
        if lock.reason == "focus_lost":
            return (
                "This attempt was locked because the quiz tab lost focus. "
                "Your answers are saved. Ask your instructor to unlock it."
            )
        if lock.reason == "saved_progress":
            return (
                "This attempt was locked when you saved your progress. "
                "Ask your instructor to unlock it."
            )
        return (
            "This attempt was locked by the instructor. "
            "Your answers are saved. Ask your instructor to unlock it."
        )
    return None


def question_section_groups(quiz):
    groups = [
        (section, [question for question in quiz.questions
                   if question.section_assignment
                   and question.section_assignment.section_id == section.id])
        for section in quiz.sections
    ]
    unsectioned = [
        question for question in quiz.questions
        if question.section_assignment is None
    ]
    if unsectioned:
        groups.append((None, unsectioned))
    return [(section, questions) for section, questions in groups if questions]


def save_attempt_answers(attempt, quiz):
    for question in quiz.questions:
        field_value = request.form.get(f"answer_{question.id}", "").strip()
        answer = QuizAnswer.query.filter_by(
            attempt_id=attempt.id,
            question_id=question.id,
        ).first()
        if not field_value:
            if answer is not None:
                db.session.delete(answer)
            continue

        if question.question_type in {"single_choice", "true_false"}:
            try:
                option_id = int(field_value)
            except ValueError:
                abort(400, description="Choose one of the listed answer options.")
            if not any(option.id == option_id for option in question.options):
                abort(400, description="The selected answer is not an option for this question.")
            if answer is None:
                answer = QuizAnswer(
                    attempt_id=attempt.id,
                    quiz_id=quiz.id,
                    question_id=question.id,
                )
                db.session.add(answer)
            answer.selected_option_id = option_id
            answer.response_text = None
        else:
            if len(field_value) > 5000:
                abort(400, description="Short answers must be 5,000 characters or fewer.")
            if answer is None:
                answer = QuizAnswer(
                    attempt_id=attempt.id,
                    quiz_id=quiz.id,
                    question_id=question.id,
                )
                db.session.add(answer)
            answer.selected_option_id = None
            answer.response_text = field_value


def ensure_attempt_open(attempt, quiz):
    if attempt.status != "in_progress":
        return False
    if attempt_lock_reason(attempt, quiz):
        return False
    if db.session.get(QuizAttemptPause, attempt.id) is not None:
        return True
    deadline = attempt_deadline(attempt, quiz)
    if deadline and datetime.now(timezone.utc) >= deadline:
        attempt.submitted_at = datetime.now(timezone.utc)
        auto_grade_attempt(attempt)
        db.session.commit()
        return False
    return True


@main.route("/student/", endpoint="student_dashboard")
@permission_required("quiz.take")
def student_dashboard():
    published_quizzes = (
        assigned_quizzes_query().filter(Quiz.is_published.is_(True))
        .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
        .order_by(Quiz.updated_at.desc())
        .limit(5)
        .all()
    )
    attempts = (
        QuizAttempt.query.filter_by(user_id=g.current_user.id)
        .filter(~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id)))
        .order_by(QuizAttempt.started_at.desc(), QuizAttempt.id.desc())
        .limit(8)
        .all()
    )
    quizzes_by_id = {
        quiz.id: quiz
        for quiz in Quiz.query.filter(
            Quiz.id.in_([attempt.quiz_id for attempt in attempts])
        ).all()
    } if attempts else {}
    return render_template(
        "student/dashboard.html",
        published_quizzes=published_quizzes,
        attempts=attempts,
        quizzes_by_id=quizzes_by_id,
    )


@main.route("/quizzes", endpoint="available_quizzes", methods=["GET"])
@permission_required("quiz.take")
def available_quizzes():
    search = request.args.get("q", "").strip()
    query = (
        assigned_quizzes_query().filter(Quiz.is_published.is_(True))
        .join(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
    )
    if search:
        query = query.filter(Quiz.title.ilike(f"%{search}%"))
    quizzes = query.order_by(Quiz.updated_at.desc(), Quiz.id.desc()).all()
    return render_template(
        "student/quizzes.html",
        quizzes=quizzes,
        search=search,
    )


@main.route("/quizzes/<int:quiz_id>", endpoint="quiz_details")
@permission_required("quiz.take")
def quiz_details(quiz_id):
    quiz = published_quiz_or_404(quiz_id)
    available_now, availability_message = availability_status(quiz)
    settings = db.session.get(QuizSettings, quiz.id)
    attempts = QuizAttempt.query.filter_by(
        quiz_id=quiz.id,
        user_id=g.current_user.id,
    ).order_by(QuizAttempt.attempt_number.desc())
    attempts = attempts.filter(
        ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id))
    )
    active_attempt = attempts.filter_by(status="in_progress").first()
    attempt_count = attempts.count()
    attempt_limit = allowed_attempt_count(quiz, g.current_user.id)
    return render_template(
        "student/quiz_details.html",
        quiz=quiz,
        active_attempt=active_attempt,
        attempt_count=attempt_count,
        attempt_limit=attempt_limit,
        available_now=available_now,
        availability_message=availability_message,
        password_required=bool(settings and settings.password_hash),
        availability_start=(
            normalize_datetime(settings.available_from)
            if settings and settings.available_from
            else None
        ),
        availability_end=(
            normalize_datetime(settings.available_until)
            if settings and settings.available_until
            else None
        ),
        total_points=sum(
            (question.points for question in quiz.questions),
            0,
        ),
        can_start=(
            active_attempt is not None
            or (
                available_now
                and (
                    attempt_limit is None
                    or attempt_count < attempt_limit
                )
            )
        ),
    )


@main.route(
    "/quizzes/<int:quiz_id>/attempts",
    methods=["POST"],
    endpoint="start_quiz_attempt",
)
@permission_required("quiz.take")
def start_quiz_attempt(quiz_id):
    quiz = published_quiz_or_404(quiz_id)
    active_attempt = QuizAttempt.query.filter_by(
        quiz_id=quiz.id,
        user_id=g.current_user.id,
        status="in_progress",
    ).filter(
        ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id))
    ).first()
    if active_attempt is not None:
        return redirect(url_for("main.take_quiz_attempt", attempt_id=active_attempt.id))
    available_now, message = availability_status(quiz)
    if not available_now:
        flash(message, "warning")
        return redirect(url_for("main.quiz_details", quiz_id=quiz.id))
    settings = db.session.get(QuizSettings, quiz.id)
    if settings and settings.password_hash and not check_password_hash(
        settings.password_hash,
        request.form.get("password", ""),
    ):
        flash("The quiz password is incorrect.", "danger")
        return redirect(url_for("main.quiz_details", quiz_id=quiz.id))
    attempt_count = valid_attempt_count(quiz.id, g.current_user.id)
    attempt_limit = allowed_attempt_count(quiz, g.current_user.id)
    if attempt_limit is not None and attempt_count >= attempt_limit:
        abort(409, description="You have used all attempts available for this quiz.")
    if not quiz.questions:
        abort(409, description="This quiz does not have any questions yet.")

    started_at = datetime.now(timezone.utc)
    last_attempt_number = (
        db.session.query(func.max(QuizAttempt.attempt_number))
        .filter_by(quiz_id=quiz.id, user_id=g.current_user.id)
        .scalar()
        or 0
    )
    attempt = QuizAttempt(
        quiz_id=quiz.id,
        user_id=g.current_user.id,
        attempt_number=last_attempt_number + 1,
        status="in_progress",
        started_at=started_at,
    )
    attempt.timing = QuizAttemptTiming(
        deadline_at=(
            started_at + timedelta(seconds=quiz.time_limit_seconds)
            if quiz.time_limit_seconds
            else None
        )
    )
    db.session.add(attempt)
    db.session.commit()
    return redirect(url_for("main.take_quiz_attempt", attempt_id=attempt.id))


@main.route("/attempts/<int:attempt_id>", endpoint="take_quiz_attempt")
@permission_required("quiz.take")
def take_quiz_attempt(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if attempt.status != "in_progress":
        return redirect(
            url_for("main.quiz_attempt_result", attempt_id=attempt.id)
        )
    lock_reason = attempt_lock_reason(attempt, quiz)
    if lock_reason:
        return render_template(
            "student/attempt_locked.html",
            attempt=attempt,
            quiz=quiz,
            lock_reason=lock_reason,
        )
    if not ensure_attempt_open(attempt, quiz):
        return redirect(
            url_for("main.quiz_attempt_result", attempt_id=attempt.id)
        )
    exam_mode = bool(quiz.exam_mode and quiz.exam_mode.enabled)
    if exam_mode and db.session.get(QuizAttemptPause, attempt.id) is None:
        db.session.add(QuizAttemptPause(attempt_id=attempt.id))
        db.session.commit()
    answers = {
        answer.question_id: answer
        for answer in QuizAnswer.query.filter_by(attempt_id=attempt.id).all()
    }
    deadline = attempt_deadline(attempt, quiz)
    remaining_seconds = (
        max(0, ceil((deadline - datetime.now(timezone.utc)).total_seconds()))
        if deadline
        else None
    )
    settings = db.session.get(QuizSettings, quiz.id)
    return render_template(
        "student/take_quiz.html",
        attempt=attempt,
        quiz=quiz,
        answers=answers,
        remaining_seconds=remaining_seconds,
        interface_theme=settings.interface_theme if settings else "classic",
        question_groups=question_section_groups(quiz),
        exam_mode=exam_mode,
        attempt_paused=(
            exam_mode
            and db.session.get(QuizAttemptPause, attempt.id) is not None
        ),
    )


@main.route(
    "/attempts/<int:attempt_id>/pause",
    methods=["POST"],
    endpoint="pause_exam_attempt",
)
@permission_required("quiz.take")
def pause_exam_attempt(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if not quiz.exam_mode or not quiz.exam_mode.enabled:
        abort(409, description="Exam mode is not enabled for this quiz.")
    if attempt.status != "in_progress":
        return jsonify({"result_url": url_for(
            "main.quiz_attempt_result",
            attempt_id=attempt.id,
        )}), 409
    if db.session.get(QuizAttemptPause, attempt.id) is not None:
        return jsonify({"paused": True})
    if not ensure_attempt_open(attempt, quiz):
        return jsonify({"result_url": url_for(
            "main.quiz_attempt_result",
            attempt_id=attempt.id,
        )}), 409
    save_attempt_answers(attempt, quiz)
    db.session.add(QuizAttemptPause(attempt_id=attempt.id))
    db.session.commit()
    return jsonify({"paused": True})


@main.route(
    "/attempts/<int:attempt_id>/focus-lost",
    methods=["POST"],
    endpoint="lock_attempt_on_focus_loss",
)
@permission_required("quiz.take")
def lock_attempt_on_focus_loss(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if not quiz.exam_mode or not quiz.exam_mode.enabled:
        abort(409, description="Exam mode is not enabled for this quiz.")
    if attempt.status != "in_progress":
        return jsonify({"result_url": url_for(
            "main.quiz_attempt_result",
            attempt_id=attempt.id,
        )}), 409
    lock = db.session.get(QuizAttemptLock, attempt.id)
    if lock is not None:
        return jsonify({"locked": True})
    if not ensure_attempt_open(attempt, quiz):
        return jsonify({"result_url": url_for(
            "main.take_quiz_attempt",
            attempt_id=attempt.id,
        )}), 409

    save_attempt_answers(attempt, quiz)
    db.session.add(
        QuizAttemptLock(attempt_id=attempt.id, reason="focus_lost")
    )
    db.session.commit()
    return jsonify({"locked": True})


@main.route(
    "/attempts/<int:attempt_id>/resume",
    methods=["POST"],
    endpoint="resume_exam_attempt",
)
@permission_required("quiz.take")
def resume_exam_attempt(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if not quiz.exam_mode or not quiz.exam_mode.enabled:
        abort(409, description="Exam mode is not enabled for this quiz.")
    if attempt.status != "in_progress":
        return jsonify({"result_url": url_for(
            "main.quiz_attempt_result",
            attempt_id=attempt.id,
        )}), 409
    if attempt_lock_reason(attempt, quiz):
        return jsonify({"result_url": url_for(
            "main.take_quiz_attempt",
            attempt_id=attempt.id,
        )}), 409

    pause = db.session.get(QuizAttemptPause, attempt.id)
    now = datetime.now(timezone.utc)
    if pause is not None:
        paused_at = normalize_datetime(pause.paused_at)
        deadline = attempt_deadline(attempt, quiz)
        if deadline is not None:
            timing = attempt.timing
            if timing is None:
                timing = QuizAttemptTiming(deadline_at=deadline)
                attempt.timing = timing
            timing.deadline_at = deadline + max(now - paused_at, timedelta(0))
        db.session.delete(pause)
        db.session.commit()
    else:
        if not ensure_attempt_open(attempt, quiz):
            return jsonify({"result_url": url_for(
                "main.quiz_attempt_result",
                attempt_id=attempt.id,
            )}), 409

    deadline = attempt_deadline(attempt, quiz)
    remaining_seconds = (
        max(0, ceil((deadline - now).total_seconds()))
        if deadline
        else None
    )
    return jsonify({"resumed": True, "remaining_seconds": remaining_seconds})


@main.route(
    "/attempts/<int:attempt_id>/answers",
    methods=["POST"],
    endpoint="save_quiz_answer",
)
@permission_required("quiz.take")
def save_quiz_answer(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if not ensure_attempt_open(attempt, quiz):
        endpoint = (
            "main.take_quiz_attempt"
            if attempt.status == "in_progress"
            else "main.quiz_attempt_result"
        )
        return redirect(url_for(endpoint, attempt_id=attempt.id))
    save_attempt_answers(attempt, quiz)
    if (
        request.form.get("save_progress") == "1"
        and quiz.exam_mode
        and quiz.exam_mode.enabled
    ):
        db.session.add(
            QuizAttemptLock(attempt_id=attempt.id, reason="saved_progress")
        )
    db.session.commit()
    return redirect(url_for("main.take_quiz_attempt", attempt_id=attempt.id))


@main.route(
    "/attempts/<int:attempt_id>/submit",
    methods=["POST"],
    endpoint="submit_quiz_attempt",
)
@permission_required("quiz.take")
def submit_quiz_attempt(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    quiz = quiz_for_attempt_or_404(attempt)
    if attempt.status != "in_progress":
        return redirect(url_for("main.quiz_attempt_result", attempt_id=attempt.id))
    if db.session.get(QuizAttemptPause, attempt.id) is not None:
        flash("The quiz is paused until full screen is restored.", "warning")
        return redirect(url_for("main.take_quiz_attempt", attempt_id=attempt.id))
    if not ensure_attempt_open(attempt, quiz):
        endpoint = (
            "main.take_quiz_attempt"
            if attempt.status == "in_progress"
            else "main.quiz_attempt_result"
        )
        return redirect(url_for(endpoint, attempt_id=attempt.id))
    save_attempt_answers(attempt, quiz)
    attempt.submitted_at = datetime.now(timezone.utc)
    auto_grade_attempt(attempt)
    db.session.commit()
    return redirect(url_for("main.quiz_attempt_result", attempt_id=attempt.id))


@main.route(
    "/attempts/<int:attempt_id>/result",
    endpoint="quiz_attempt_result",
)
@permission_required("quiz.take")
def quiz_attempt_result(attempt_id):
    attempt = owned_attempt_or_404(attempt_id)
    if attempt.status == "in_progress":
        return redirect(url_for("main.take_quiz_attempt", attempt_id=attempt.id))
    quiz = quiz_for_attempt_or_404(attempt)
    answers = {
        answer.question_id: answer
        for answer in QuizAnswer.query.filter_by(attempt_id=attempt.id).all()
    }
    attempt_count = valid_attempt_count(quiz.id, g.current_user.id)
    attempt_limit = allowed_attempt_count(quiz, g.current_user.id)
    available_now, _ = availability_status(quiz)
    return render_template(
        "student/quiz_result.html",
        attempt=attempt,
        quiz=quiz,
        answers=answers,
        total_points=sum(
            (question.points for question in quiz.questions),
            0,
        ),
        can_retake=(
            available_now
            and (
                attempt_limit is None
                or attempt_count < attempt_limit
            )
        ),
    )
