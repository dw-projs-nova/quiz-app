from decimal import Decimal, InvalidOperation
import secrets
import string
from datetime import datetime, timezone

from flask import abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from werkzeug.security import generate_password_hash

from controllers.shared import has_permission, main, permission_required
from controllers.quiz_grading import normalize_short_answer
from models import (
    ClassEnrollment,
    ClassGroup,
    QuestionOption,
    QuizClassAssignment,
    QuizAcceptedAnswer,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizAttemptAllowance,
    QuizAttemptInvalidation,
    QuizExamMode,
    QuizAttemptLock,
    QuizClosure,
    QuizQuestion,
    QuizQuestionSection,
    QuizSection,
    QuizSettings,
    QuizStudentAssignment,
    User,
    db,
)


QUESTION_TYPES = {
    "single_choice": "Multiple choice",
    "true_false": "True or false",
    "short_answer": "Short answer",
}
QUIZ_THEMES = {
    "classic": "Classic cards",
    "focus": "Focus mode",
    "compact": "Compact list",
}


def managed_quiz_or_404(quiz_id, *, load_question_details=False):
    query = Quiz.query.filter_by(id=quiz_id)
    if not has_permission("quiz.manage_all"):
        query = query.filter_by(instructor_id=g.current_user.id)
    if load_question_details:
        query = query.options(
            selectinload(Quiz.questions).selectinload(QuizQuestion.options),
            selectinload(Quiz.questions).selectinload(
                QuizQuestion.accepted_answers
            ),
            selectinload(Quiz.questions)
            .selectinload(QuizQuestion.section_assignment)
            .selectinload(QuizQuestionSection.section),
            selectinload(Quiz.sections),
        )
    return query.first_or_404()


def quiz_form_values(quiz=None, form=None):
    if form is not None:
        return {
            "title": form.get("title", "").strip(),
            "description": form.get("description", "").strip(),
            "time_limit_minutes": form.get("time_limit_minutes", "").strip(),
            "max_attempts": form.get("max_attempts", "").strip(),
            "available_from": form.get("available_from", "").strip(),
            "available_until": form.get("available_until", "").strip(),
            "password": form.get("password", ""),
            "clear_password": form.get("clear_password") == "on",
            "interface_theme": form.get("interface_theme", "classic"),
            "exam_mode": form.get("exam_mode") == "on",
            "has_password": False,
        }
    settings = quiz.settings if quiz and quiz.settings else None
    return {
        "title": quiz.title if quiz else "",
        "description": quiz.description or "" if quiz else "",
        "time_limit_minutes": (
            str(quiz.time_limit_seconds // 60)
            if quiz and quiz.time_limit_seconds
            else ""
        ),
        "max_attempts": str(quiz.max_attempts) if quiz and quiz.max_attempts else "",
        "available_from": (
            utc_datetime_iso(settings.available_from)
            if settings and settings.available_from
            else ""
        ),
        "available_until": (
            utc_datetime_iso(settings.available_until)
            if settings and settings.available_until
            else ""
        ),
        "password": "",
        "clear_password": False,
        "has_password": bool(settings and settings.password_hash),
        "interface_theme": settings.interface_theme if settings else "classic",
        "exam_mode": bool(quiz and quiz.exam_mode and quiz.exam_mode.enabled),
    }


def utc_datetime_iso(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def validate_quiz_values(values):
    values.setdefault("has_password", False)
    if not values["title"] or len(values["title"]) > 200:
        return "Enter a title of 1 to 200 characters.", None, None
    if len(values["description"]) > 10000:
        return "The description must be 10,000 characters or fewer.", None, None
    if len(values["password"]) > 128:
        return "Quiz passwords must be 128 characters or fewer.", None, None

    time_limit = None
    max_attempts = None
    try:
        if values["time_limit_minutes"]:
            time_limit = int(values["time_limit_minutes"]) * 60
            if time_limit <= 0 or time_limit > 604800:
                raise ValueError
    except ValueError:
        return "Enter a time limit from 1 to 10,080 minutes, or leave it blank.", None, None
    try:
        if values["max_attempts"]:
            max_attempts = int(values["max_attempts"])
            if max_attempts <= 0:
                raise ValueError
    except ValueError:
        return "Maximum attempts must be a positive whole number, or left blank.", None, None
    available_from = parse_utc_datetime(values["available_from"])
    available_until = parse_utc_datetime(values["available_until"])
    if values["available_from"] and available_from is None:
        return "Choose a valid availability start time.", None, None
    if values["available_until"] and available_until is None:
        return "Choose a valid availability end time.", None, None
    if available_from and available_until and available_until <= available_from:
        return "Availability must end after it starts.", None, None
    if values["interface_theme"] not in QUIZ_THEMES:
        return "Choose one of the listed quiz interfaces.", None, None
    return None, time_limit, max_attempts


def parse_utc_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def get_or_create_quiz_settings(quiz):
    settings = quiz.settings
    if settings is None:
        settings = QuizSettings(quiz=quiz)
        db.session.add(settings)
    return settings


def save_quiz_settings(quiz, values, available_from, available_until):
    settings = get_or_create_quiz_settings(quiz)
    settings.available_from = available_from
    settings.available_until = available_until
    settings.interface_theme = values["interface_theme"]
    if values["clear_password"]:
        settings.password_hash = None
    elif values["password"]:
        settings.password_hash = generate_password_hash(values["password"])
    exam_mode = quiz.exam_mode
    if values["exam_mode"] and exam_mode is None:
        db.session.add(QuizExamMode(quiz=quiz))
    elif not values["exam_mode"] and exam_mode is not None:
        db.session.delete(exam_mode)


def question_section_groups(quiz):
    questions_by_section = {}
    unsectioned = []
    for question in quiz.questions:
        assignment = question.section_assignment
        if assignment is None:
            unsectioned.append(question)
        else:
            questions_by_section.setdefault(assignment.section_id, []).append(
                question
            )

    groups = [
        (section, questions_by_section.get(section.id, []))
        for section in quiz.sections
        if questions_by_section.get(section.id)
    ]
    if unsectioned:
        groups.append((None, unsectioned))
    return groups


def assign_question_section(quiz, question, raw_section_id):
    if not raw_section_id:
        if question.section_assignment is not None:
            db.session.delete(question.section_assignment)
        return
    try:
        section_id = int(raw_section_id)
    except ValueError:
        abort(400, description="Choose a valid section.")
    section = QuizSection.query.filter_by(
        id=section_id,
        quiz_id=quiz.id,
    ).first()
    if section is None:
        abort(400, description="Choose a section belonging to this quiz.")
    if question.section_assignment is None:
        question.section_assignment = QuizQuestionSection(section=section)
    else:
        question.section_assignment.section = section


def replace_question_options(question, options, correct_option):
    existing_options = list(question.options)
    removed_options = existing_options[len(options):]
    removed_ids = [option.id for option in removed_options]
    if removed_ids and QuizAnswer.query.filter(
        QuizAnswer.selected_option_id.in_(removed_ids)
    ).first():
        return "An answer choice used in a previous attempt cannot be removed."

    next_position = max(
        (option.position for option in existing_options),
        default=0,
    ) + len(existing_options) + 1
    for offset, option in enumerate(existing_options):
        option.position = next_position + offset
    db.session.flush()

    for position, text in enumerate(options, start=1):
        if position <= len(existing_options):
            option = existing_options[position - 1]
        else:
            option = QuestionOption(question=question)
        option.position = position
        option.text = text
        option.is_correct = position == correct_option

    for option in removed_options:
        question.options.remove(option)
    return None


def question_form_values(form):
    question_type = form.get("question_type", "single_choice")
    return {
        "prompt": form.get("prompt", "").strip(),
        "question_type": question_type,
        "points": form.get("points", "1").strip(),
        "options": form.get("options", ""),
        "accepted_answers": form.get("accepted_answers", ""),
        "section_id": form.get("section_id", "").strip(),
        "correct_option": form.get(
            "true_false_answer" if question_type == "true_false" else "correct_option",
            "1",
        ).strip(),
    }


def validate_question_values(values):
    if not values["prompt"] or len(values["prompt"]) > 5000:
        return "Enter a question of 1 to 5,000 characters.", None, None
    if values["question_type"] not in QUESTION_TYPES:
        return "Choose one of the listed answer types.", None, None
    try:
        points = Decimal(values["points"])
        if not points.is_finite() or points <= 0 or points > Decimal("999999.99"):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return "Enter a valid positive points value.", None, None

    options = []
    correct_option = 0
    if values["question_type"] == "single_choice":
        options = [line.strip() for line in values["options"].splitlines() if line.strip()]
        try:
            correct_option = int(values["correct_option"])
        except ValueError:
            return "Choose the number of the correct option.", None, None
        if len(options) < 2:
            return "Multiple-choice questions need at least two options.", None, None
        if not 1 <= correct_option <= len(options):
            return "The correct option number must match an option in the list.", None, None
    elif values["question_type"] == "true_false":
        options = ["True", "False"]
        try:
            correct_option = int(values["correct_option"])
        except ValueError:
            return "Choose whether the correct answer is True or False.", None, None
        if correct_option not in (1, 2):
            return "Choose True or False as the correct answer.", None, None

    accepted_answers = []
    if values["question_type"] == "short_answer":
        accepted_answers = [
            line.strip()
            for line in values["accepted_answers"].splitlines()
            if line.strip()
        ]
        if any(len(answer) > 500 for answer in accepted_answers):
            return "Accepted answers must be 500 characters or fewer each.", None, None
        normalized_answers = {answer.casefold() for answer in accepted_answers}
        if len(normalized_answers) != len(accepted_answers):
            return "Remove duplicate accepted answers.", None, None
        if len(accepted_answers) > 20:
            return "Add no more than 20 accepted answers.", None, None

    return None, points, (options, correct_option, accepted_answers)


def quiz_has_attempts(quiz):
    return QuizAttempt.query.filter_by(quiz_id=quiz.id).count() > 0


def quiz_has_active_attempts(quiz):
    return QuizAttempt.query.filter_by(
        quiz_id=quiz.id,
        status="in_progress",
    ).count() > 0


def question_content_is_locked(quiz):
    return quiz.is_published or quiz_has_active_attempts(quiz)


def ensure_question_content_unlocked(quiz):
    if quiz.is_published:
        abort(409, description="Unpublish the quiz before changing its questions.")
    if quiz_has_active_attempts(quiz):
        abort(
            409,
            description="Question content cannot change while students have active attempts.",
        )


def renumber_items(items):
    if not items:
        return
    position_offset = max(item.position for item in items) + len(items) + 1
    for item in items:
        item.position += position_offset
    db.session.flush()
    for position, item in enumerate(items, start=1):
        item.position = position


def managed_class_or_404(class_id):
    return ClassGroup.query.filter_by(
        id=class_id,
        instructor_id=g.current_user.id,
    ).first_or_404()


def instructor_student_query():
    return (
        User.query.join(ClassEnrollment, ClassEnrollment.student_id == User.id)
        .join(ClassGroup, ClassGroup.id == ClassEnrollment.class_id)
        .filter(
            ClassGroup.instructor_id == g.current_user.id,
            User.role == "student",
        )
        .distinct()
    )


def managed_student_or_404(student_id):
    return instructor_student_query().filter(User.id == student_id).first_or_404()


@main.route("/instructor/classes", methods=["GET", "POST"], endpoint="instructor_classes")
@permission_required("quiz.create")
def instructor_classes():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name or len(name) > 120:
            abort(400, description="Enter a class name of 1 to 120 characters.")
        alphabet = string.ascii_uppercase + string.digits
        class_code = "".join(secrets.choice(alphabet) for _ in range(8))
        while ClassGroup.query.filter_by(class_code=class_code).first() is not None:
            class_code = "".join(secrets.choice(alphabet) for _ in range(8))
        classroom = ClassGroup(
            instructor_id=g.current_user.id,
            name=name,
            class_code=class_code,
        )
        db.session.add(classroom)
        db.session.commit()
        return redirect(
            url_for("main.instructor_classroom", class_id=classroom.id)
        )
    classes = ClassGroup.query.filter_by(
        instructor_id=g.current_user.id
    ).order_by(ClassGroup.created_at.desc(), ClassGroup.id.desc()).all()
    return render_template("instructor/classes.html", classes=classes)


@main.route(
    "/instructor/classes/<int:class_id>",
    methods=["GET", "POST"],
    endpoint="instructor_classroom",
)
@permission_required("quiz.create")
def instructor_classroom(class_id):
    classroom = managed_class_or_404(class_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "assign_quiz":
            quiz_id = request.form.get("quiz_id", type=int)
            quiz = Quiz.query.filter_by(
                id=quiz_id,
                instructor_id=g.current_user.id,
            ).first_or_404()
            existing = QuizClassAssignment.query.filter_by(
                quiz_id=quiz.id,
                class_id=classroom.id,
            ).first()
            if existing is None:
                db.session.add(
                    QuizClassAssignment(quiz_id=quiz.id, class_id=classroom.id)
                )
                db.session.commit()
                flash(f"{quiz.title} was assigned to {classroom.name}.", "success")
            else:
                flash(f"{quiz.title} is already assigned to {classroom.name}.", "info")
        elif action == "remove_quiz":
            assignment = QuizClassAssignment.query.filter_by(
                id=request.form.get("assignment_id", type=int),
                class_id=classroom.id,
            ).first_or_404()
            quiz = Quiz.query.filter_by(
                id=assignment.quiz_id,
                instructor_id=g.current_user.id,
            ).first_or_404()
            db.session.delete(assignment)
            db.session.commit()
            flash(f"{quiz.title} was removed from {classroom.name}.", "success")
        elif action == "assign_quiz_to_students":
            quiz = Quiz.query.filter_by(
                id=request.form.get("quiz_id", type=int),
                instructor_id=g.current_user.id,
            ).first_or_404()
            try:
                student_ids = {
                    int(raw_student_id)
                    for raw_student_id in request.form.getlist("student_ids")
                }
            except ValueError:
                abort(400, description="Choose valid students.")
            if any(student_id <= 0 for student_id in student_ids):
                abort(400, description="Choose valid students.")
            if not student_ids:
                abort(400, description="Select at least one student.")
            enrolled_ids = {
                student_id
                for (student_id,) in db.session.query(ClassEnrollment.student_id)
                .join(User, User.id == ClassEnrollment.student_id)
                .filter(
                    ClassEnrollment.class_id == classroom.id,
                    ClassEnrollment.student_id.in_(student_ids),
                    User.role == "student",
                )
                .all()
            }
            if enrolled_ids != student_ids:
                abort(400, description="Select students enrolled in this class.")
            existing_ids = {
                assignment.student_id
                for assignment in QuizStudentAssignment.query.filter(
                    QuizStudentAssignment.quiz_id == quiz.id,
                    QuizStudentAssignment.student_id.in_(student_ids),
                ).all()
            }
            new_student_ids = student_ids - existing_ids
            db.session.add_all(
                QuizStudentAssignment(quiz_id=quiz.id, student_id=student_id)
                for student_id in new_student_ids
            )
            db.session.commit()
            if new_student_ids:
                flash(
                    f"Quiz assigned individually to {len(new_student_ids)} student(s).",
                    "success",
                )
            else:
                flash("Those students already have this individual assignment.", "info")
        elif action == "remove_student_quiz":
            enrollment = ClassEnrollment.query.filter_by(
                class_id=classroom.id,
                student_id=request.form.get("student_id", type=int),
            ).first_or_404()
            assignment = QuizStudentAssignment.query.filter_by(
                id=request.form.get("assignment_id", type=int),
                student_id=enrollment.student_id,
            ).first_or_404()
            quiz = Quiz.query.filter_by(
                id=assignment.quiz_id,
                instructor_id=g.current_user.id,
            ).first_or_404()
            db.session.delete(assignment)
            db.session.commit()
            flash(
                f"Individual assignment for {quiz.title} was removed.",
                "success",
            )
        else:
            abort(400, description="Choose a valid class action.")
        return redirect(url_for("main.instructor_classroom", class_id=classroom.id))

    assigned_ids = {
        assignment.quiz_id for assignment in classroom.quiz_assignments
    }
    quizzes = Quiz.query.filter_by(
        instructor_id=g.current_user.id
    ).order_by(Quiz.title, Quiz.id).all()
    unassigned_quizzes = [
        quiz for quiz in quizzes if quiz.id not in assigned_ids
    ]
    individual_assignments = {}
    if classroom.enrollments:
        assignments = (
            QuizStudentAssignment.query.join(Quiz)
            .filter(
                Quiz.instructor_id == g.current_user.id,
                QuizStudentAssignment.student_id.in_(
                    [enrollment.student_id for enrollment in classroom.enrollments]
                ),
            )
            .order_by(Quiz.title, Quiz.id)
            .all()
        )
        for assignment in assignments:
            individual_assignments.setdefault(assignment.student_id, []).append(
                assignment
            )
    return render_template(
        "instructor/classroom.html",
        classroom=classroom,
        quizzes=quizzes,
        unassigned_quizzes=unassigned_quizzes,
        assigned_ids=assigned_ids,
        individual_assignments=individual_assignments,
    )


@main.route("/classes", endpoint="student_classes")
@permission_required("quiz.take")
def student_classes():
    enrollments = ClassEnrollment.query.filter_by(
        student_id=g.current_user.id
    ).order_by(ClassEnrollment.joined_at.desc()).all()
    return render_template("student/classes.html", enrollments=enrollments)


@main.route("/classes/join", methods=["POST"], endpoint="join_class")
@permission_required("quiz.take")
def join_class():
    if g.current_user.role != "student":
        abort(403)
    class_code = request.form.get("class_code", "").strip().upper()
    classroom = ClassGroup.query.filter_by(class_code=class_code).first()
    if classroom is None:
        flash("That class code was not found.", "danger")
        return redirect(url_for("main.student_classes"))
    enrollment = ClassEnrollment.query.filter_by(
        class_id=classroom.id,
        student_id=g.current_user.id,
    ).first()
    if enrollment is None:
        db.session.add(
            ClassEnrollment(class_id=classroom.id, student_id=g.current_user.id)
        )
        db.session.commit()
    flash(f"You joined {classroom.name}.", "success")
    return redirect(url_for("main.student_classes"))


@main.route("/instructor/", endpoint="instructor_dashboard")
@permission_required("quiz.create")
def instructor_dashboard():
    query = Quiz.query
    if not has_permission("quiz.manage_all"):
        query = query.filter_by(instructor_id=g.current_user.id)
    quizzes = query.order_by(Quiz.updated_at.desc(), Quiz.id.desc()).all()
    quiz_ids = [quiz.id for quiz in quizzes]
    attempt_counts = dict(
        db.session.query(QuizAttempt.quiz_id, func.count(QuizAttempt.id))
        .filter(
            QuizAttempt.quiz_id.in_(quiz_ids),
            ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id)),
        )
        .group_by(QuizAttempt.quiz_id)
        .all()
    ) if quiz_ids else {}
    pending_count = (
        db.session.query(func.count(QuizAttempt.id))
        .filter(
            QuizAttempt.quiz_id.in_(quiz_ids),
            QuizAttempt.status == "submitted",
            ~QuizAttempt.id.in_(select(QuizAttemptInvalidation.attempt_id)),
        )
        .scalar()
        or 0
    ) if quiz_ids else 0
    published_count = sum(quiz.is_published for quiz in quizzes)
    return render_template(
        "instructor/dashboard.html",
        quizzes=quizzes[:5],
        quiz_count=len(quizzes),
        published_count=published_count,
        draft_count=len(quizzes) - published_count,
        attempt_counts=attempt_counts,
        total_attempts=sum(attempt_counts.values()),
        pending_count=pending_count,
    )


@main.route("/instructor/quizzes", endpoint="instructor_quizzes")
@permission_required("quiz.manage_own")
def instructor_quizzes():
    query = Quiz.query
    if not has_permission("quiz.manage_all"):
        query = query.filter_by(instructor_id=g.current_user.id)
    status_filter = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    if status_filter == "published":
        query = query.filter_by(is_published=True)
    elif status_filter == "draft":
        query = query.filter_by(is_published=False)
    if search:
        query = query.filter(Quiz.title.ilike(f"%{search}%"))
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
        attempt_counts=dict(
            db.session.query(QuizAttempt.quiz_id, func.count(QuizAttempt.id))
            .filter(QuizAttempt.quiz_id.in_([quiz.id for quiz in quizzes]))
            .group_by(QuizAttempt.quiz_id)
            .all()
        ) if quizzes else {},
        status_filter=status_filter,
        search=search,
    )


@main.route(
    "/instructor/quizzes/new",
    methods=["GET", "POST"],
    endpoint="create_quiz",
)
@permission_required("quiz.create")
def create_quiz():
    values = quiz_form_values()
    error = None
    if request.method == "POST":
        values = quiz_form_values(form=request.form)
        error, time_limit, max_attempts = validate_quiz_values(values)
        if error is None:
            quiz = Quiz(
                instructor_id=g.current_user.id,
                title=values["title"],
                description=values["description"] or None,
                time_limit_seconds=time_limit,
                max_attempts=max_attempts,
            )
            save_quiz_settings(
                quiz,
                values,
                parse_utc_datetime(values["available_from"]),
                parse_utc_datetime(values["available_until"]),
            )
            db.session.add(quiz)
            db.session.commit()
            return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/quiz_form.html",
        quiz=None,
        values=values,
        quiz_themes=QUIZ_THEMES,
        error=error,
    ), 400 if error else 200


@main.route(
    "/instructor/quizzes/<int:quiz_id>",
    endpoint="instructor_quiz_details",
)
@permission_required("quiz.manage_own")
def instructor_quiz_details(quiz_id):
    quiz = managed_quiz_or_404(quiz_id, load_question_details=True)
    attempt_count = QuizAttempt.query.filter_by(quiz_id=quiz.id).count()
    active_attempt_count = QuizAttempt.query.filter_by(
        quiz_id=quiz.id,
        status="in_progress",
    ).count()
    classes = ClassGroup.query.filter_by(
        instructor_id=g.current_user.id
    ).order_by(ClassGroup.name, ClassGroup.id).all()
    students = instructor_student_query().order_by(
        User.display_name, User.id
    ).all()
    question_values = {
        "prompt": "",
        "question_type": "single_choice",
        "points": "1",
        "options": "",
        "accepted_answers": "",
        "section_id": "",
        "correct_option": "1",
    }
    return render_template(
        "instructor/quiz_detail.html",
        quiz=quiz,
        question_types=QUESTION_TYPES,
        question_values=question_values,
        error=None,
        question_sections=question_section_groups(quiz),
        attempt_count=attempt_count,
        active_attempt_count=active_attempt_count,
        question_content_locked=quiz.is_published or active_attempt_count > 0,
        questions_with_answers={
            question_id
            for (question_id,) in db.session.query(QuizAnswer.question_id)
            .filter_by(quiz_id=quiz.id)
            .distinct()
            .all()
        },
        total_points=sum((question.points for question in quiz.questions), Decimal(0)),
        assignment_classes=classes,
        assignment_students=students,
        individual_assignments=(
            QuizStudentAssignment.query.join(User)
            .filter(QuizStudentAssignment.quiz_id == quiz.id)
            .order_by(User.display_name, User.id)
            .all()
        ),
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/assignments",
    methods=["POST"],
    endpoint="manage_quiz_assignments",
)
@permission_required("quiz.manage_own")
def manage_quiz_assignments(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    action = request.form.get("action")
    if action == "assign_class":
        classroom = managed_class_or_404(request.form.get("class_id", type=int))
        existing = QuizClassAssignment.query.filter_by(
            quiz_id=quiz.id,
            class_id=classroom.id,
        ).first()
        if existing is None:
            db.session.add(
                QuizClassAssignment(quiz_id=quiz.id, class_id=classroom.id)
            )
            db.session.commit()
            flash(f"{quiz.title} was assigned to {classroom.name}.", "success")
        else:
            flash(f"{quiz.title} is already assigned to {classroom.name}.", "info")
    elif action == "remove_class":
        assignment = QuizClassAssignment.query.filter_by(
            id=request.form.get("assignment_id", type=int),
            quiz_id=quiz.id,
        ).first_or_404()
        classroom = managed_class_or_404(assignment.class_id)
        db.session.delete(assignment)
        db.session.commit()
        flash(f"{quiz.title} was removed from {classroom.name}.", "success")
    elif action == "assign_student":
        student = managed_student_or_404(request.form.get("student_id", type=int))
        existing = QuizStudentAssignment.query.filter_by(
            quiz_id=quiz.id,
            student_id=student.id,
        ).first()
        if existing is None:
            db.session.add(
                QuizStudentAssignment(quiz_id=quiz.id, student_id=student.id)
            )
            db.session.commit()
            flash(f"{quiz.title} was assigned to {student.display_name}.", "success")
        else:
            flash(
                f"{quiz.title} is already individually assigned to "
                f"{student.display_name}.",
                "info",
            )
    elif action == "remove_student":
        assignment = QuizStudentAssignment.query.filter_by(
            id=request.form.get("assignment_id", type=int),
            quiz_id=quiz.id,
        ).first_or_404()
        student_name = assignment.student.display_name
        db.session.delete(assignment)
        db.session.commit()
        flash(
            f"The individual assignment for {student_name} was removed.",
            "success",
        )
    else:
        abort(400, description="Choose a valid assignment action.")
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/sections",
    methods=["POST"],
    endpoint="create_quiz_section",
)
@permission_required("quiz.manage_own")
def create_quiz_section(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    ensure_question_content_unlocked(quiz)
    title = request.form.get("title", "").strip()
    if not title or len(title) > 120:
        abort(400, description="Enter a section title of 1 to 120 characters.")
    position = (max((section.position for section in quiz.sections), default=0) + 1)
    db.session.add(QuizSection(quiz_id=quiz.id, position=position, title=title))
    db.session.commit()
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/sections/<int:section_id>/delete",
    methods=["POST"],
    endpoint="delete_quiz_section",
)
@permission_required("quiz.manage_own")
def delete_quiz_section(quiz_id, section_id):
    quiz = managed_quiz_or_404(quiz_id)
    ensure_question_content_unlocked(quiz)
    section = QuizSection.query.filter_by(
        id=section_id,
        quiz_id=quiz.id,
    ).first_or_404()
    db.session.delete(section)
    db.session.flush()
    for position, remaining in enumerate(quiz.sections, start=1):
        remaining.position = position
    db.session.commit()
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/edit",
    methods=["GET", "POST"],
    endpoint="edit_quiz",
)
@permission_required("quiz.manage_own")
def edit_quiz(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    error = None
    values = quiz_form_values(quiz)
    if request.method == "POST":
        values = quiz_form_values(form=request.form)
        values["has_password"] = bool(
            quiz.settings and quiz.settings.password_hash
        )
        error, time_limit, max_attempts = validate_quiz_values(values)
        if error is None:
            quiz.title = values["title"]
            quiz.description = values["description"] or None
            quiz.time_limit_seconds = time_limit
            quiz.max_attempts = max_attempts
            save_quiz_settings(
                quiz,
                values,
                parse_utc_datetime(values["available_from"]),
                parse_utc_datetime(values["available_until"]),
            )
            db.session.commit()
            return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/quiz_form.html",
        quiz=quiz,
        values=values,
        quiz_themes=QUIZ_THEMES,
        error=error,
    ), 400 if error else 200


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/new",
    methods=["POST"],
    endpoint="create_quiz_question",
)
@permission_required("quiz.manage_own")
def create_quiz_question(quiz_id):
    quiz = managed_quiz_or_404(quiz_id, load_question_details=True)
    ensure_question_content_unlocked(quiz)
    values = question_form_values(request.form)
    error, points, choice_data = validate_question_values(values)
    if error is None:
        options, correct_option, accepted_answers = choice_data
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
        for position, answer in enumerate(accepted_answers, start=1):
            question.accepted_answers.append(
                QuizAcceptedAnswer(position=position, answer=answer)
            )
        db.session.add(question)
        db.session.flush()
        assign_question_section(quiz, question, values["section_id"])
        db.session.commit()
        return redirect(
            url_for("main.instructor_quiz_details", quiz_id=quiz.id)
            + "#question-builder"
        )

    return render_template(
        "instructor/quiz_detail.html",
        quiz=quiz,
        question_types=QUESTION_TYPES,
        question_values=values,
        error=error,
        question_sections=question_section_groups(quiz),
        attempt_count=QuizAttempt.query.filter_by(quiz_id=quiz.id).count(),
        active_attempt_count=QuizAttempt.query.filter_by(
            quiz_id=quiz.id,
            status="in_progress",
        ).count(),
        question_content_locked=question_content_is_locked(quiz),
        questions_with_answers={
            question_id
            for (question_id,) in db.session.query(QuizAnswer.question_id)
            .filter_by(quiz_id=quiz.id)
            .distinct()
            .all()
        },
        total_points=sum((question.points for question in quiz.questions), Decimal(0)),
    ), 400


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/<int:question_id>/edit",
    methods=["GET", "POST"],
    endpoint="edit_quiz_question",
)
@permission_required("quiz.manage_own")
def edit_quiz_question(quiz_id, question_id):
    quiz = managed_quiz_or_404(quiz_id)
    question = QuizQuestion.query.filter_by(
        id=question_id,
        quiz_id=quiz.id,
    ).first_or_404()
    ensure_question_content_unlocked(quiz)

    values = {
        "prompt": question.prompt,
        "question_type": question.question_type,
        "points": str(question.points),
        "options": "\n".join(option.text for option in question.options),
        "accepted_answers": "\n".join(
            item.answer for item in question.accepted_answers
        ),
        "section_id": (
            str(question.section_assignment.section_id)
            if question.section_assignment
            else ""
        ),
        "correct_option": str(
            next(
                (option.position for option in question.options if option.is_correct),
                1,
            )
        ),
    }
    error = None
    if request.method == "POST":
        values = question_form_values(request.form)
        error, points, choice_data = validate_question_values(values)
        if error is None:
            options, correct_option, accepted_answers = choice_data
            error = replace_question_options(question, options, correct_option)
            if error is None:
                question.prompt = values["prompt"]
                question.question_type = values["question_type"]
                question.points = points
                question.accepted_answers.clear()
                db.session.flush()
                for position, answer in enumerate(accepted_answers, start=1):
                    question.accepted_answers.append(
                        QuizAcceptedAnswer(position=position, answer=answer)
                    )
                assign_question_section(quiz, question, values["section_id"])
                db.session.commit()
                return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    return render_template(
        "instructor/question_form.html",
        quiz=quiz,
        question=question,
        question_types=QUESTION_TYPES,
        sections=quiz.sections,
        question_values=values,
        error=error,
    ), 400 if error else 200


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/<int:question_id>/delete",
    methods=["POST"],
    endpoint="delete_quiz_question",
)
@permission_required("quiz.manage_own")
def delete_quiz_question(quiz_id, question_id):
    quiz = managed_quiz_or_404(quiz_id)
    question = QuizQuestion.query.filter_by(
        id=question_id,
        quiz_id=quiz.id,
    ).first_or_404()
    ensure_question_content_unlocked(quiz)
    if QuizAnswer.query.filter_by(
        quiz_id=quiz.id,
        question_id=question.id,
    ).first():
        abort(
            409,
            description="A question with recorded responses cannot be deleted.",
        )
    db.session.delete(question)
    db.session.flush()
    remaining_questions = (
        QuizQuestion.query.filter_by(quiz_id=quiz.id)
        .order_by(QuizQuestion.position)
        .all()
    )
    renumber_items(remaining_questions)
    db.session.commit()
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/fix-issues",
    methods=["POST"],
    endpoint="fix_quiz_issues",
)
@permission_required("quiz.manage_own")
def fix_quiz_issues(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    ensure_question_content_unlocked(quiz)
    questions = (
        QuizQuestion.query.filter_by(quiz_id=quiz.id)
        .order_by(QuizQuestion.position, QuizQuestion.id)
        .all()
    )
    sections = (
        QuizSection.query.filter_by(quiz_id=quiz.id)
        .order_by(QuizSection.position, QuizSection.id)
        .all()
    )
    renumber_items(questions)
    renumber_items(sections)
    db.session.commit()
    flash("Question and section numbers are now in order.", "success")
    return redirect(
        url_for("main.instructor_quiz_details", quiz_id=quiz.id) + "#questions"
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/questions/<int:question_id>/move",
    methods=["POST"],
    endpoint="move_quiz_question",
)
@permission_required("quiz.manage_own")
def move_quiz_question(quiz_id, question_id):
    quiz = managed_quiz_or_404(quiz_id)
    ensure_question_content_unlocked(quiz)
    questions = list(quiz.questions)
    current_index = next(
        (index for index, question in enumerate(questions) if question.id == question_id),
        None,
    )
    if current_index is None:
        abort(404)
    direction = request.form.get("direction")
    neighbor_index = current_index - 1 if direction == "up" else current_index + 1
    if direction not in {"up", "down"}:
        abort(400, description="Choose whether to move the question up or down.")
    if not 0 <= neighbor_index < len(questions):
        return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))

    question = questions[current_index]
    neighbor = questions[neighbor_index]
    current_position = question.position
    neighbor_position = neighbor.position
    temporary_position = max(item.position for item in questions) + 1
    question.position = temporary_position
    db.session.flush()
    neighbor.position = temporary_position + 1
    db.session.flush()
    question.position = neighbor_position
    db.session.flush()
    neighbor.position = current_position
    db.session.commit()
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/publish",
    methods=["POST"],
    endpoint="publish_quiz",
)
@permission_required("quiz.manage_own")
def publish_quiz(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    if quiz.is_published:
        quiz.is_published = False
    else:
        if not quiz.questions:
            abort(400, description="Add at least one question before publishing.")
        for question in quiz.questions:
            if question.question_type == "single_choice" and (
                len(question.options) < 2
                or not any(option.is_correct for option in question.options)
            ):
                abort(
                    400,
                    description="Each multiple-choice question needs at least two options and one correct answer.",
                )
            if question.question_type == "true_false" and not any(
                option.is_correct for option in question.options
            ):
                abort(
                    400,
                    description="Each true/false question needs a correct answer.",
                )
        quiz.is_published = True
    db.session.commit()
    return redirect(url_for("main.instructor_quiz_details", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/delete",
    methods=["POST"],
    endpoint="delete_quiz",
)
@permission_required("quiz.manage_own")
def delete_quiz(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    if quiz_has_attempts(quiz):
        abort(409, description="A quiz with student attempts cannot be deleted.")
    db.session.delete(quiz)
    db.session.commit()
    return redirect(url_for("main.instructor_quizzes"))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts",
    endpoint="quiz_attempts",
)
@permission_required("quiz.manage_own")
def quiz_attempts(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    attempts = (
        db.session.query(QuizAttempt, User)
        .join(User, User.id == QuizAttempt.user_id)
        .filter(QuizAttempt.quiz_id == quiz.id)
        .order_by(QuizAttempt.started_at.desc(), QuizAttempt.id.desc())
        .all()
    )
    attempt_locks = {
        lock.attempt_id: lock
        for lock in QuizAttemptLock.query.join(
            QuizAttempt,
            QuizAttempt.id == QuizAttemptLock.attempt_id,
        ).filter(QuizAttempt.quiz_id == quiz.id).all()
    }
    invalidations = {
        invalidation.attempt_id: invalidation
        for invalidation in QuizAttemptInvalidation.query.join(
            QuizAttempt,
            QuizAttempt.id == QuizAttemptInvalidation.attempt_id,
        ).filter(QuizAttempt.quiz_id == quiz.id).all()
    }
    attempt_allowances = {
        allowance.user_id: allowance.additional_attempts
        for allowance in QuizAttemptAllowance.query.filter_by(
            quiz_id=quiz.id,
        ).all()
    }
    return render_template(
        "instructor/attempts.html",
        quiz=quiz,
        attempts=attempts,
        attempt_locks=attempt_locks,
        invalidations=invalidations,
        attempt_allowances=attempt_allowances,
        quiz_ended=db.session.get(QuizClosure, quiz.id) is not None,
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/control",
    methods=["POST"],
    endpoint="control_quiz_attempts",
)
@permission_required("quiz.manage_own")
def control_quiz_attempts(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    action = request.form.get("action")
    closure = db.session.get(QuizClosure, quiz.id)
    if closure is not None and action not in {"reopen_quiz"}:
        abort(409, description="Reopen the quiz before changing attempt locks.")
    active_attempts = QuizAttempt.query.filter_by(
        quiz_id=quiz.id,
        status="in_progress",
    ).all()
    existing_locks = {
        lock.attempt_id: lock
        for lock in QuizAttemptLock.query.filter(
            QuizAttemptLock.attempt_id.in_(
                [attempt.id for attempt in active_attempts]
            )
        ).all()
    } if active_attempts else {}

    if action == "lock_all":
        for attempt in active_attempts:
            if attempt.id not in existing_locks:
                db.session.add(
                    QuizAttemptLock(attempt_id=attempt.id, reason="instructor")
                )
        flash("All active attempts for this quiz are locked.", "warning")
    elif action == "unlock_all":
        for lock in existing_locks.values():
            if lock.reason in {"instructor", "focus_lost", "saved_progress"}:
                db.session.delete(lock)
        flash("Active attempt locks were removed.", "success")
    elif action == "end_quiz":
        if closure is not None:
            abort(409, description="This quiz has already ended.")
        db.session.add(QuizClosure(quiz_id=quiz.id))
        for attempt in active_attempts:
            if attempt.id not in existing_locks:
                db.session.add(
                    QuizAttemptLock(attempt_id=attempt.id, reason="quiz_ended")
                )
        flash(
            "The quiz has ended. Active attempts are locked and remain unsubmitted.",
            "warning",
        )
    elif action == "reopen_quiz":
        if closure is None:
            abort(409, description="This quiz is not ended.")
        db.session.delete(closure)
        for lock in QuizAttemptLock.query.join(
            QuizAttempt,
            QuizAttempt.id == QuizAttemptLock.attempt_id,
        ).filter(
            QuizAttempt.quiz_id == quiz.id,
            QuizAttemptLock.reason == "quiz_ended",
        ).all():
            db.session.delete(lock)
        flash("The quiz has reopened. Previously locked attempts may continue.", "success")
    else:
        abort(400, description="Choose a valid attempt control.")

    db.session.commit()
    return redirect(url_for("main.quiz_attempts", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/lock",
    methods=["POST"],
    endpoint="lock_quiz_attempt",
)
@permission_required("quiz.manage_own")
def lock_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    if db.session.get(QuizClosure, quiz.id) is not None:
        abort(409, description="The quiz has ended.")
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
        status="in_progress",
    ).first_or_404()
    lock = db.session.get(QuizAttemptLock, attempt.id)
    if lock is None:
        db.session.add(
            QuizAttemptLock(attempt_id=attempt.id, reason="instructor")
        )
        db.session.commit()
        flash("The student's active attempt is locked.", "warning")
    return redirect(url_for("main.quiz_attempts", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/unlock",
    methods=["POST"],
    endpoint="unlock_quiz_attempt",
)
@permission_required("quiz.manage_own")
def unlock_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    if db.session.get(QuizClosure, quiz.id) is not None:
        abort(409, description="Reopen the quiz before unlocking attempts.")
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
        status="in_progress",
    ).first_or_404()
    lock = db.session.get(QuizAttemptLock, attempt.id)
    if lock is not None and lock.reason in {
        "instructor",
        "focus_lost",
        "saved_progress",
    }:
        db.session.delete(lock)
        db.session.commit()
        flash("The student's attempt is unlocked.", "success")
    return redirect(url_for("main.quiz_attempts", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/grant",
    methods=["POST"],
    endpoint="grant_quiz_attempt",
)
@permission_required("quiz.manage_own")
def grant_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    if quiz.max_attempts is None:
        abort(409, description="This quiz already allows unlimited attempts.")
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
    ).first_or_404()
    if db.session.get(QuizAttemptInvalidation, attempt.id) is not None:
        abort(409, description="Cannot grant attempts from an invalidated attempt.")
    allowance = db.session.get(
        QuizAttemptAllowance,
        (quiz.id, attempt.user_id),
    )
    if allowance is None:
        allowance = QuizAttemptAllowance(
            quiz_id=quiz.id,
            user_id=attempt.user_id,
            additional_attempts=1,
        )
        db.session.add(allowance)
    else:
        allowance.additional_attempts += 1
    db.session.commit()
    flash(
        f"Granted {allowance.additional_attempts} additional "
        f"attempt{'s' if allowance.additional_attempts != 1 else ''} to "
        f"{db.session.get(User, attempt.user_id).display_name}.",
        "success",
    )
    return redirect(url_for("main.quiz_attempts", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/invalidate",
    methods=["POST"],
    endpoint="invalidate_quiz_attempt",
)
@permission_required("quiz.manage_own")
def invalidate_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
    ).first_or_404()
    if db.session.get(QuizAttemptInvalidation, attempt.id) is not None:
        abort(409, description="This attempt has already been invalidated.")
    db.session.add(
        QuizAttemptInvalidation(
            attempt_id=attempt.id,
            invalidated_by_id=g.current_user.id,
        )
    )
    db.session.commit()
    flash(
        f"Attempt #{attempt.attempt_number} for "
        f"{db.session.get(User, attempt.user_id).display_name} was invalidated. "
        "The attempt and answers remain available for instructor review, but no "
        "longer count toward the student's attempt limit.",
        "warning",
    )
    return redirect(url_for("main.quiz_attempts", quiz_id=quiz.id))


@main.route(
    "/instructor/quizzes/<int:quiz_id>/report",
    endpoint="quiz_report",
)
@permission_required("quiz.manage_own")
def quiz_report(quiz_id):
    quiz = managed_quiz_or_404(quiz_id)
    invalid_attempt_ids = select(QuizAttemptInvalidation.attempt_id)
    attempts = QuizAttempt.query.filter_by(quiz_id=quiz.id).filter(
        ~QuizAttempt.id.in_(invalid_attempt_ids)
    ).all()
    scores = [attempt.score for attempt in attempts if attempt.score is not None]
    average_score = sum(scores, Decimal(0)) / len(scores) if scores else None
    question_stats = []
    for question in quiz.questions:
        correct_option = next(
            (option for option in question.options if option.is_correct),
            None,
        )
        answered_count = (
            db.session.query(func.count(QuizAnswer.id))
            .join(QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id)
            .filter(
                QuizAttempt.quiz_id == quiz.id,
                ~QuizAttempt.id.in_(invalid_attempt_ids),
                QuizAnswer.question_id == question.id,
            )
            .scalar()
            or 0
        )
        if question.question_type == "short_answer" and question.accepted_answers:
            accepted = {
                normalize_short_answer(item.answer)
                for item in question.accepted_answers
            }
            responses = (
                QuizAnswer.query.join(
                    QuizAttempt,
                    QuizAttempt.id == QuizAnswer.attempt_id,
                )
                .filter(
                    QuizAttempt.quiz_id == quiz.id,
                    ~QuizAttempt.id.in_(invalid_attempt_ids),
                    QuizAnswer.question_id == question.id,
                )
                .all()
            )
            correct_count = sum(
                answer.response_text is not None
                and normalize_short_answer(answer.response_text) in accepted
                for answer in responses
            )
            correct_percent = (
                round(correct_count * 100 / answered_count)
                if answered_count
                else None
            )
        elif correct_option is not None:
            correct_count = (
                db.session.query(func.count(QuizAnswer.id))
                .join(QuizAttempt, QuizAttempt.id == QuizAnswer.attempt_id)
                .filter(
                    QuizAttempt.quiz_id == quiz.id,
                    ~QuizAttempt.id.in_(invalid_attempt_ids),
                    QuizAnswer.question_id == question.id,
                    QuizAnswer.selected_option_id == correct_option.id,
                )
                .scalar()
                or 0
            )
            correct_percent = (
                round(correct_count * 100 / answered_count)
                if answered_count
                else None
            )
        else:
            correct_count = 0
            correct_percent = None
        question_stats.append(
            {
                "question": question,
                "answered_count": answered_count,
                "correct_count": correct_count,
                "correct_percent": correct_percent,
            }
        )
    return render_template(
        "instructor/report.html",
        quiz=quiz,
        attempt_count=len(attempts),
        graded_count=sum(attempt.status == "graded" for attempt in attempts),
        pending_count=sum(attempt.status == "submitted" for attempt in attempts),
        average_score=average_score,
        total_points=sum((question.points for question in quiz.questions), Decimal(0)),
        question_stats=question_stats,
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>",
    endpoint="review_quiz_attempt",
)
@permission_required("quiz.manage_own")
def review_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
    ).first_or_404()
    student = db.session.get(User, attempt.user_id)
    answers = {
        answer.question_id: answer
        for answer in QuizAnswer.query.filter_by(attempt_id=attempt.id).all()
    }
    return render_template(
        "instructor/attempt_review.html",
        quiz=quiz,
        attempt=attempt,
        student=student,
        answers=answers,
        invalidated=(
            db.session.get(QuizAttemptInvalidation, attempt.id) is not None
        ),
    )


@main.route(
    "/instructor/quizzes/<int:quiz_id>/attempts/<int:attempt_id>/grade",
    methods=["POST"],
    endpoint="grade_quiz_attempt",
)
@permission_required("quiz.manage_own")
def grade_quiz_attempt(quiz_id, attempt_id):
    quiz = managed_quiz_or_404(quiz_id)
    attempt = QuizAttempt.query.filter_by(
        id=attempt_id,
        quiz_id=quiz.id,
    ).first_or_404()
    if db.session.get(QuizAttemptInvalidation, attempt.id) is not None:
        abort(409, description="Invalidated attempts cannot be graded.")
    if attempt.status not in {"submitted", "graded"}:
        abort(409, description="Only submitted attempts can be graded.")

    answers = QuizAnswer.query.filter_by(attempt_id=attempt.id).all()
    questions = {question.id: question for question in quiz.questions}
    try:
        awarded_points = {
            answer.id: Decimal(request.form.get(f"points_{answer.id}", ""))
            for answer in answers
        }
    except (InvalidOperation, TypeError, ValueError):
        abort(400, description="Enter a valid points value for every answer.")
    for answer in answers:
        points = awarded_points[answer.id]
        question = questions.get(answer.question_id)
        if (
            not points.is_finite()
            or points < 0
            or question is None
            or points > question.points
        ):
            abort(400, description="Points must be between zero and the question maximum.")
    for answer in answers:
        answer.points_awarded = awarded_points[answer.id]
    attempt.score = sum(awarded_points.values(), Decimal(0))
    attempt.status = "graded"
    db.session.commit()
    return redirect(
        url_for(
            "main.review_quiz_attempt",
            quiz_id=quiz.id,
            attempt_id=attempt.id,
        )
    )
