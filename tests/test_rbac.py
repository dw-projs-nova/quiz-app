from datetime import datetime, timedelta, timezone
import re
import unittest
from urllib.parse import urlparse

from flask import url_for

from app import create_app
from controllers.auth import login_serializer, token_hash
from controllers.shared import ROLE_PERMISSIONS
from models import LoginToken, PendingLoginToken, Quiz, QuizQuestion, QuestionOption, User, db


class RbacTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite://",
                "ADMIN_EMAIL": "bootstrap@example.com",
            }
        )
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            self.student = User(
                email="student@example.com",
                display_name="Student",
                role="student",
            )
            self.instructor = User(
                email="instructor@example.com",
                display_name="Instructor",
                role="instructor",
            )
            self.admin = User(
                email="admin@example.com",
                display_name="Admin",
                role="admin",
            )
            db.session.add_all([self.student, self.instructor, self.admin])
            db.session.commit()
            self.student_id = self.student.id
            self.instructor_id = self.instructor.id
            self.admin_id = self.admin.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()

    def sign_in_as(self, user_id):
        with self.client.session_transaction() as browser_session:
            browser_session["user_id"] = user_id

    def csrf_token(self):
        with self.client.session_transaction() as browser_session:
            return browser_session.setdefault("csrf_token", "test-csrf-token")

    def test_role_permissions_are_scoped(self):
        self.assertEqual(ROLE_PERMISSIONS["student"], {"quiz.take"})
        self.assertIn("quiz.create", ROLE_PERMISSIONS["instructor"])
        self.assertNotIn("users.manage", ROLE_PERMISSIONS["instructor"])
        self.assertIn("users.manage", ROLE_PERMISSIONS["admin"])
        self.assertIn("roles.assign", ROLE_PERMISSIONS["admin"])

    def test_user_management_requires_login_and_admin_permission(self):
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

        self.sign_in_as(self.student_id)
        self.assertEqual(self.client.get("/admin/users").status_code, 403)

        self.sign_in_as(self.instructor_id)
        self.assertEqual(self.client.get("/admin/users").status_code, 403)

        self.sign_in_as(self.admin_id)
        response = self.client.get("/admin/users")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Manage users", response.data)

    def test_flask_admin_backend_requires_admin_permission(self):
        self.assertEqual(self.client.get("/admin/").status_code, 302)

        self.sign_in_as(self.student_id)
        self.assertEqual(self.client.get("/admin/").status_code, 403)
        self.assertEqual(self.client.get("/admin/user/").status_code, 403)

        self.sign_in_as(self.admin_id)
        with self.app.test_request_context():
            admin_url = url_for("quiz_admin.index")
        self.assertEqual(admin_url, "/admin/")
        admin_account_page = self.client.get("/account")
        self.assertIn(b'href="/admin/"', admin_account_page.data)
        self.assertEqual(admin_account_page.data.count(b">Admin dashboard</a>"), 2)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Quiz App Admin", response.data)
        self.assertIn(b"admin-halfmoon.css", response.data)
        self.assertIn(b"data-admin-theme", response.data)
        self.assertIn(b"Users", response.data)
        dashboard_response = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard_response.status_code, 302)
        self.assertEqual(dashboard_response.headers["Location"], "/admin/")
        stylesheet = self.client.get("/Static/styles/admin-halfmoon.css")
        try:
            self.assertEqual(stylesheet.status_code, 200)
        finally:
            stylesheet.close()
        users_response = self.client.get("/admin/user/")
        self.assertEqual(users_response.status_code, 200)
        self.assertIn(b"student@example.com", users_response.data)

    def test_flask_admin_user_view_creates_users_and_protects_last_admin(self):
        self.sign_in_as(self.admin_id)
        form_response = self.client.get("/admin/user/new/")
        self.assertEqual(form_response.status_code, 200)
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            form_response.data,
        )
        self.assertIsNotNone(csrf_token)

        created = self.client.post(
            "/admin/user/new/",
            data={
                "csrf_token": csrf_token.group(1).decode(),
                "email": "created-in-admin@example.com",
                "display_name": "Created in Admin",
                "role": "student",
            },
        )
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            created_user = User.query.filter_by(
                email="created-in-admin@example.com"
            ).one()
            self.assertEqual(created_user.role, "student")
            admin = db.session.get(User, self.admin_id)
            admin_email = admin.email
            admin_display_name = admin.display_name

        edit_response = self.client.get(f"/admin/user/edit/?id={self.admin_id}")
        self.assertEqual(edit_response.status_code, 200)
        edit_csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            edit_response.data,
        )
        self.assertIsNotNone(edit_csrf_token)
        demoted = self.client.post(
            f"/admin/user/edit/?id={self.admin_id}",
            data={
                "csrf_token": edit_csrf_token.group(1).decode(),
                "email": admin_email,
                "display_name": admin_display_name,
                "role": "student",
            },
        )
        self.assertEqual(demoted.status_code, 409)

    def test_flask_admin_model_forms_use_application_csrf_token(self):
        self.sign_in_as(self.admin_id)
        response = self.client.get("/admin/quiz/new/")
        self.assertEqual(response.status_code, 200)
        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(csrf_token)
        accepted = self.client.post(
            "/admin/quiz/new/",
            data={
                "csrf_token": csrf_token.group(1).decode(),
                "instructor_id": str(self.instructor_id),
                "title": "Admin-created quiz",
            },
        )
        self.assertEqual(accepted.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                Quiz.query.filter_by(title="Admin-created quiz").count(),
                1,
            )
        self.assertEqual(
            self.client.post("/admin/quiz/new/", data={"title": "No token"}).status_code,
            400,
        )

    def test_flask_admin_question_form_has_type_choices_and_inline_options(self):
        with self.app.app_context():
            quiz = Quiz(
                instructor_id=self.instructor_id,
                title="Question admin quiz",
            )
            db.session.add(quiz)
            db.session.commit()
            quiz_id = quiz.id

        self.sign_in_as(self.admin_id)
        form_response = self.client.get("/admin/question/new/")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn(b'name="question_type"', form_response.data)
        self.assertIn(b'Multiple choice (one correct answer)', form_response.data)
        self.assertIn(b'True or false', form_response.data)
        self.assertIn(b'Short answer', form_response.data)
        self.assertIn(b'id="options"', form_response.data)
        self.assertIn(b'name="quiz"', form_response.data)

        csrf_token = re.search(
            rb'name="csrf_token" value="([^"]+)"',
            form_response.data,
        )
        self.assertIsNotNone(csrf_token)
        created = self.client.post(
            "/admin/question/new/",
            data={
                "csrf_token": csrf_token.group(1).decode(),
                "quiz": str(quiz_id),
                "position": "1",
                "prompt": "Which option is correct?",
                "question_type": "single_choice",
                "points": "2",
                "options-0-position": "1",
                "options-0-text": "First option",
                "options-0-is_correct": "y",
            },
        )
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            question = QuizQuestion.query.filter_by(
                quiz_id=quiz_id,
                prompt="Which option is correct?",
            ).one()
            self.assertEqual(len(question.options), 1)
            self.assertEqual(question.options[0].text, "First option")
            self.assertTrue(question.options[0].is_correct)

    def test_instructor_can_create_and_view_only_their_own_quizzes(self):
        response = self.client.get("/instructor/quizzes")
        self.assertEqual(response.status_code, 302)

        self.sign_in_as(self.student_id)
        self.assertEqual(self.client.get("/instructor/quizzes").status_code, 403)

        self.sign_in_as(self.instructor_id)
        created = self.client.post(
            "/instructor/quizzes/new",
            data={
                "csrf_token": self.csrf_token(),
                "title": "Instructor quiz",
                "description": "A basic quiz.",
            },
        )
        self.assertEqual(created.status_code, 302)
        with self.app.app_context():
            quiz = Quiz.query.filter_by(title="Instructor quiz").one()
            quiz_id = quiz.id
            self.assertEqual(quiz.instructor_id, self.instructor_id)
            other_instructor = User(
                email="another-instructor@example.com",
                display_name="Another Instructor",
                role="instructor",
            )
            db.session.add(other_instructor)
            db.session.flush()
            other_quiz = Quiz(
                instructor_id=other_instructor.id,
                title="Someone else's quiz",
            )
            db.session.add(other_quiz)
            db.session.commit()
            other_instructor_id = other_instructor.id
            other_quiz_id = other_quiz.id

        listing = self.client.get("/instructor/quizzes")
        self.assertEqual(listing.status_code, 200)
        self.assertIn(b"Instructor quiz", listing.data)
        self.assertIn(b"Questions", listing.data)
        self.assertIn(b"Draft", listing.data)
        details = self.client.get(f"/instructor/quizzes/{quiz_id}")
        self.assertEqual(details.status_code, 200)
        self.assertIn(b"Add a question", details.data)

        self.sign_in_as(other_instructor_id)
        other_listing = self.client.get("/instructor/quizzes")
        self.assertNotIn(b"Instructor quiz", other_listing.data)
        self.assertIn(b"Someone else", other_listing.data)
        self.assertEqual(self.client.get(f"/instructor/quizzes/{quiz_id}").status_code, 404)
        self.assertEqual(
            self.client.get(f"/instructor/quizzes/{other_quiz_id}").status_code,
            200,
        )

    def test_instructor_can_add_basic_question_with_explained_options(self):
        with self.app.app_context():
            quiz = Quiz(
                instructor_id=self.instructor_id,
                title="Question maker quiz",
            )
            db.session.add(quiz)
            db.session.commit()
            quiz_id = quiz.id

        self.sign_in_as(self.instructor_id)
        details = self.client.get(f"/instructor/quizzes/{quiz_id}")
        self.assertEqual(details.status_code, 200)
        self.assertIn(b"one answer the student can choose", details.data)
        self.assertIn(b"one choice per line", details.data)
        self.assertIn(b"Correct option number", details.data)

        added = self.client.post(
            f"/instructor/quizzes/{quiz_id}/questions/new",
            data={
                "csrf_token": self.csrf_token(),
                "prompt": "Pick the correct answer",
                "question_type": "single_choice",
                "points": "2",
                "options": "First answer\nSecond answer",
                "correct_option": "2",
            },
        )
        self.assertEqual(added.status_code, 302)
        with self.app.app_context():
            question = QuizQuestion.query.filter_by(
                quiz_id=quiz_id,
                prompt="Pick the correct answer",
            ).one()
            self.assertEqual(
                [(option.text, option.is_correct) for option in question.options],
                [("First answer", False), ("Second answer", True)],
            )

        added_true_false = self.client.post(
            f"/instructor/quizzes/{quiz_id}/questions/new",
            data={
                "csrf_token": self.csrf_token(),
                "prompt": "The sky is blue.",
                "question_type": "true_false",
                "points": "1",
                "true_false_answer": "1",
            },
        )
        self.assertEqual(added_true_false.status_code, 302)
        with self.app.app_context():
            true_false = QuizQuestion.query.filter_by(
                quiz_id=quiz_id,
                question_type="true_false",
            ).one()
            self.assertEqual(
                [(option.text, option.is_correct) for option in true_false.options],
                [("True", True), ("False", False)],
            )

    def test_navbar_uses_responsive_collapse_and_role_gated_links(self):
        self.sign_in_as(self.student_id)
        student_page = self.client.get("/account")
        self.assertIn(b'class="collapse navbar-collapse" id="main-navigation"', student_page.data)
        self.assertIn(b'data-bs-target="#main-navigation"', student_page.data)
        self.assertIn(b'href="/account"', student_page.data)
        self.assertNotIn(b'href="/admin/users"', student_page.data)
        self.assertNotIn(b"Admin backend", student_page.data)
        self.assertIn(b'name="csrf_token"', student_page.data)

        self.sign_in_as(self.instructor_id)
        instructor_page = self.client.get("/account")
        self.assertIn(b'href="/instructor/quizzes"', instructor_page.data)

        self.sign_in_as(self.admin_id)
        admin_page = self.client.get("/admin/users")
        self.assertIn(b'href="/admin/users"', admin_page.data)
        self.assertIn(b'href="/admin/"', admin_page.data)

    def test_administrator_can_assign_roles(self):
        self.sign_in_as(self.admin_id)
        response = self.client.post(
            "/admin/users",
            data={
                "csrf_token": self.csrf_token(),
                "user_id": self.student_id,
                "role": "instructor",
            },
        )
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.student_id).role, "instructor")

    def test_last_administrator_cannot_be_demoted(self):
        self.sign_in_as(self.admin_id)
        response = self.client.post(
            "/admin/users",
            data={
                "csrf_token": self.csrf_token(),
                "user_id": self.admin_id,
                "role": "student",
            },
        )
        self.assertEqual(response.status_code, 409)
        with self.app.app_context():
            self.assertEqual(db.session.get(User, self.admin_id).role, "admin")

    def test_profile_displays_effective_permissions(self):
        self.sign_in_as(self.instructor_id)
        response = self.client.get("/account")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"quiz.create", response.data)
        self.assertNotIn(b"users.manage", response.data)

    def test_new_accounts_are_created_only_after_verified_login(self):
        from unittest.mock import patch

        with patch("controllers.auth.send_login_email") as send_login_email:
            student_response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(),
                    "email": "new-student@example.com",
                },
            )
            admin_response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(),
                    "email": "bootstrap@example.com",
                },
            )

        self.assertEqual(student_response.status_code, 200)
        self.assertEqual(admin_response.status_code, 200)
        student_token = urlparse(send_login_email.call_args_list[0].args[1]).path.rsplit(
            "/", 1
        )[1]
        admin_token = urlparse(send_login_email.call_args_list[1].args[1]).path.rsplit(
            "/", 1
        )[1]
        with self.app.app_context():
            self.assertIsNone(
                User.query.filter_by(email="new-student@example.com").first()
            )
            self.assertIsNone(
                User.query.filter_by(email="bootstrap@example.com").first()
            )
            self.assertEqual(PendingLoginToken.query.count(), 2)

        self.client.post(
            f"/login/verify/{student_token}",
            data={"csrf_token": self.csrf_token()},
        )
        with self.app.app_context():
            student = User.query.filter_by(email="new-student@example.com").one()
            self.assertEqual(student.role, "student")

        with self.client.session_transaction() as browser_session:
            browser_session.clear()
            browser_session["csrf_token"] = "test-csrf-token"
        self.client.post(
            f"/login/verify/{admin_token}",
            data={"csrf_token": self.csrf_token()},
        )
        with self.app.app_context():
            admin = User.query.filter_by(email="bootstrap@example.com").one()
            self.assertEqual(admin.role, "admin")

    def test_configured_existing_account_is_not_promoted_until_verification(self):
        from unittest.mock import patch

        with self.app.app_context():
            user = User(
                email="bootstrap@example.com",
                display_name="Bootstrap",
                role="student",
            )
            db.session.add(user)
            db.session.commit()
            user_id = user.id

        with patch("controllers.auth.send_login_email") as send_login_email:
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf_token(),
                    "email": "bootstrap@example.com",
                },
            )
        self.assertEqual(response.status_code, 200)
        token = urlparse(send_login_email.call_args.args[1]).path.rsplit("/", 1)[1]
        with self.app.app_context():
            self.assertEqual(db.session.get(User, user_id).role, "student")

        self.client.post(
            f"/login/verify/{token}",
            data={"csrf_token": self.csrf_token()},
        )
        with self.app.app_context():
            self.assertEqual(db.session.get(User, user_id).role, "admin")

    def test_login_link_is_one_time_and_does_not_store_token_in_browser(self):
        csrf_token = self.csrf_token()
        with self.app.app_context():
            user = db.session.get(User, self.student_id)
            token = login_serializer().dumps(
                {"user_id": user.id, "nonce": "single-use"}
            )
            db.session.add(
                LoginToken(
                    token_hash=token_hash(token),
                    user_id=user.id,
                    expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    + timedelta(minutes=30),
                )
            )
            db.session.commit()

        verify_url = f"/login/verify/{token}"
        confirmation = self.client.get(verify_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(confirmation.headers["Cache-Control"], "no-store")

        completed = self.client.post(verify_url, data={"csrf_token": csrf_token})
        self.assertEqual(completed.status_code, 302)
        self.assertEqual(completed.headers["Location"], "/account")
        csrf_token = self.csrf_token()
        self.assertEqual(
            self.client.post(verify_url, data={"csrf_token": csrf_token}).status_code,
            400,
        )

    def test_state_changes_require_csrf_token(self):
        self.sign_in_as(self.admin_id)
        response = self.client.post(
            "/admin/users",
            data={"user_id": self.student_id, "role": "admin"},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
