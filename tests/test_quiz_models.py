import unittest
from datetime import datetime, timezone

from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError

from models import (
    QuestionOption,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    User,
    db,
)


class QuizModelTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        with self.app.app_context():
            event.listen(db.engine, "connect", self.enable_sqlite_foreign_keys)
            db.create_all()

            instructor = User(
                email="instructor@example.com",
                display_name="Instructor",
                role="instructor",
            )
            student = User(
                email="student@example.com",
                display_name="Student",
                role="student",
            )
            other_instructor = User(
                email="other-instructor@example.com",
                display_name="Other Instructor",
                role="instructor",
            )
            db.session.add_all([instructor, student, other_instructor])
            db.session.flush()

            self.quiz = Quiz(
                instructor_id=instructor.id,
                title="Geography",
                time_limit_seconds=900,
                max_attempts=2,
            )
            self.other_quiz = Quiz(
                instructor_id=other_instructor.id,
                title="History",
            )
            db.session.add_all([self.quiz, self.other_quiz])
            db.session.flush()

            self.question = QuizQuestion(
                quiz_id=self.quiz.id,
                position=1,
                prompt="Which is a continent?",
                question_type="single_choice",
                points=2,
            )
            self.other_question = QuizQuestion(
                quiz_id=self.other_quiz.id,
                position=1,
                prompt="Which is a historical era?",
                question_type="single_choice",
            )
            db.session.add_all([self.question, self.other_question])
            db.session.flush()

            self.option = QuestionOption(
                question_id=self.question.id,
                position=1,
                text="Africa",
                is_correct=True,
            )
            db.session.add(self.option)
            db.session.flush()

            self.attempt = QuizAttempt(
                quiz_id=self.quiz.id,
                user_id=student.id,
                attempt_number=1,
                started_at=datetime.now(timezone.utc),
            )
            db.session.add(self.attempt)
            db.session.commit()

            self.quiz_id = self.quiz.id
            self.other_quiz_id = self.other_quiz.id
            self.question_id = self.question.id
            self.other_question_id = self.other_question.id
            self.option_id = self.option.id
            self.attempt_id = self.attempt.id

    @staticmethod
    def enable_sqlite_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def assert_rejected_by_database(self, record):
        with self.app.app_context():
            db.session.add(record)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def assert_statement_rejected(self, statement, parameters):
        with self.app.app_context():
            with self.assertRaises(IntegrityError):
                db.session.execute(text(statement), parameters)
                db.session.commit()
            db.session.rollback()

    def test_quiz_schema_tables_are_declared(self):
        with self.app.app_context():
            self.assertTrue(
                {
                    "quizzes",
                    "quiz_questions",
                    "question_options",
                    "quiz_attempts",
                    "quiz_answers",
                }.issubset(db.metadata.tables)
            )

    def test_checks_reject_invalid_quiz_question_and_attempt_values(self):
        self.assert_rejected_by_database(
            Quiz(instructor_id=1, title="   ")
        )
        self.assert_rejected_by_database(
            Quiz(instructor_id=1, title="Invalid timer", time_limit_seconds=0)
        )
        self.assert_rejected_by_database(
            Quiz(instructor_id=1, title="Invalid attempts", max_attempts=0)
        )
        self.assert_rejected_by_database(
            QuizQuestion(
                quiz_id=self.quiz_id,
                position=0,
                prompt="Invalid position",
            )
        )
        self.assert_rejected_by_database(
            QuizQuestion(
                quiz_id=self.quiz_id,
                position=2,
                prompt="   ",
            )
        )
        self.assert_rejected_by_database(
            QuizQuestion(
                quiz_id=self.quiz_id,
                position=2,
                prompt="No points",
                points=0,
            )
        )
        self.assert_rejected_by_database(
            QuizQuestion(
                quiz_id=self.quiz_id,
                position=2,
                prompt="Unsupported type",
                question_type="essay",
            )
        )
        self.assert_rejected_by_database(
            QuestionOption(
                question_id=self.question_id,
                position=2,
                text="   ",
            )
        )
        self.assert_rejected_by_database(
            QuizAttempt(
                quiz_id=self.quiz_id,
                user_id=2,
                attempt_number=1,
                status="completed",
                started_at=datetime.now(timezone.utc),
            )
        )
        self.assert_rejected_by_database(
            QuizAttempt(
                quiz_id=self.quiz_id,
                user_id=2,
                attempt_number=0,
                started_at=datetime.now(timezone.utc),
            )
        )
        self.assert_rejected_by_database(
            QuizAttempt(
                quiz_id=self.quiz_id,
                user_id=2,
                attempt_number=2,
                score=-1,
                started_at=datetime.now(timezone.utc),
            )
        )
        self.assert_rejected_by_database(
            QuizAttempt(
                quiz_id=self.quiz_id,
                user_id=2,
                attempt_number=2,
                started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                submitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )

    def test_question_and_option_positions_are_unique_within_parent(self):
        self.assert_rejected_by_database(
            QuizQuestion(
                quiz_id=self.quiz_id,
                position=1,
                prompt="Duplicate position",
            )
        )
        self.assert_rejected_by_database(
            QuestionOption(
                question_id=self.question_id,
                position=1,
                text="Duplicate option position",
            )
        )
        self.assert_statement_rejected(
            "INSERT INTO question_options "
            "(question_id, position, text, is_correct) "
            "VALUES (:question_id, 2, 'Invalid boolean', 2)",
            {"question_id": self.question_id},
        )

    def test_answers_require_a_response_and_match_attempt_question_and_option(self):
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
            )
        )
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
                selected_option_id=self.option_id,
                response_text="Conflicting response",
            )
        )
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
                response_text="   ",
            )
        )
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.other_question_id,
                response_text="Question belongs to another quiz",
            )
        )
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
                selected_option_id=self.option_id + 100,
            )
        )
        self.assert_rejected_by_database(
            QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
                response_text="Negative score",
                points_awarded=-1,
            )
        )

        with self.app.app_context():
            answer = QuizAnswer(
                attempt_id=self.attempt_id,
                quiz_id=self.quiz_id,
                question_id=self.question_id,
                selected_option_id=self.option_id,
                points_awarded=2,
            )
            db.session.add(answer)
            db.session.commit()
            self.assertIsNotNone(answer.id)


if __name__ == "__main__":
    unittest.main()
