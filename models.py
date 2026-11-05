from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKeyConstraint


db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint(
            "role IN ('student', 'instructor', 'admin')",
            name="check_user_role",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True)
    display_name = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student")
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class LoginToken(db.Model):
    __tablename__ = "login_tokens"

    token_hash = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)


class PendingLoginToken(db.Model):
    __tablename__ = "pending_login_tokens"

    token_hash = db.Column(db.String(64), primary_key=True)
    email = db.Column(db.String(320), nullable=False, index=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)


class Quiz(db.Model):
    __tablename__ = "quizzes"
    __table_args__ = (
        db.CheckConstraint(
            "length(trim(title)) > 0",
            name="check_quiz_title_not_empty",
        ),
        db.CheckConstraint(
            "is_published IN (false, true)",
            name="check_quiz_published_boolean",
        ),
        db.CheckConstraint(
            "time_limit_seconds IS NULL OR time_limit_seconds > 0",
            name="check_quiz_time_limit_positive",
        ),
        db.CheckConstraint(
            "max_attempts IS NULL OR max_attempts > 0",
            name="check_quiz_max_attempts_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    instructor_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    time_limit_seconds = db.Column(db.Integer, nullable=True)
    max_attempts = db.Column(db.Integer, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    questions = db.relationship(
        "QuizQuestion",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="QuizQuestion.position",
    )


class QuizQuestion(db.Model):
    __tablename__ = "quiz_questions"
    __table_args__ = (
        db.UniqueConstraint("quiz_id", "position", name="uq_question_quiz_position"),
        db.UniqueConstraint("id", "quiz_id", name="uq_question_id_quiz"),
        db.CheckConstraint(
            "position > 0",
            name="check_question_position_positive",
        ),
        db.CheckConstraint(
            "length(trim(prompt)) > 0",
            name="check_question_prompt_not_empty",
        ),
        db.CheckConstraint(
            "question_type IN ('single_choice', 'true_false', 'short_answer')",
            name="check_question_type",
        ),
        db.CheckConstraint(
            "points > 0",
            name="check_question_points_positive",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(
        db.Integer,
        db.ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = db.Column(db.Integer, nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(20), nullable=False, default="single_choice")
    points = db.Column(db.Numeric(8, 2), nullable=False, default=1)
    quiz = db.relationship("Quiz", back_populates="questions")
    options = db.relationship(
        "QuestionOption",
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="QuestionOption.position",
    )


class QuestionOption(db.Model):
    __tablename__ = "question_options"
    __table_args__ = (
        db.UniqueConstraint(
            "question_id",
            "position",
            name="uq_option_question_position",
        ),
        db.UniqueConstraint("question_id", "id", name="uq_option_question_id"),
        db.CheckConstraint(
            "position > 0",
            name="check_option_position_positive",
        ),
        db.CheckConstraint(
            "length(trim(text)) > 0",
            name="check_option_text_not_empty",
        ),
        db.CheckConstraint(
            "is_correct IN (false, true)",
            name="check_option_correct_boolean",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(
        db.Integer,
        db.ForeignKey("quiz_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = db.Column(db.Integer, nullable=False)
    text = db.Column(db.Text, nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    question = db.relationship("QuizQuestion", back_populates="options")


class QuizAttempt(db.Model):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        db.UniqueConstraint(
            "quiz_id",
            "user_id",
            "attempt_number",
            name="uq_attempt_quiz_user_number",
        ),
        db.UniqueConstraint("id", "quiz_id", name="uq_attempt_id_quiz"),
        db.CheckConstraint(
            "attempt_number > 0",
            name="check_attempt_number_positive",
        ),
        db.CheckConstraint(
            "status IN ('in_progress', 'submitted', 'graded')",
            name="check_attempt_status",
        ),
        db.CheckConstraint(
            "score IS NULL OR score >= 0",
            name="check_attempt_score_nonnegative",
        ),
        db.CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= started_at",
            name="check_attempt_submission_after_start",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(
        db.Integer,
        db.ForeignKey("quizzes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    attempt_number = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="in_progress")
    started_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    score = db.Column(db.Numeric(10, 2), nullable=True)


class QuizAnswer(db.Model):
    __tablename__ = "quiz_answers"
    __table_args__ = (
        db.UniqueConstraint(
            "attempt_id",
            "question_id",
            name="uq_answer_attempt_question",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "quiz_id"],
            ["quiz_attempts.id", "quiz_attempts.quiz_id"],
            name="fk_answer_attempt_quiz",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "quiz_id"],
            ["quiz_questions.id", "quiz_questions.quiz_id"],
            name="fk_answer_question_quiz",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["question_id", "selected_option_id"],
            ["question_options.question_id", "question_options.id"],
            name="fk_answer_option_question",
            ondelete="RESTRICT",
        ),
        db.CheckConstraint(
            "(selected_option_id IS NOT NULL AND response_text IS NULL) OR "
            "(selected_option_id IS NULL AND response_text IS NOT NULL "
            "AND length(trim(response_text)) > 0)",
            name="check_answer_has_single_response",
        ),
        db.CheckConstraint(
            "points_awarded IS NULL OR points_awarded >= 0",
            name="check_answer_points_nonnegative",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, nullable=False, index=True)
    quiz_id = db.Column(db.Integer, nullable=False)
    question_id = db.Column(db.Integer, nullable=False, index=True)
    selected_option_id = db.Column(db.Integer, nullable=True)
    response_text = db.Column(db.Text, nullable=True)
    points_awarded = db.Column(db.Numeric(8, 2), nullable=True)
