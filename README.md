# Quiz App user management

This project includes passwordless email sign-in, user profiles, and role-based access control. Copy `.env.example` to `.env`, set a unique `SECRET_KEY`, configure SMTP, and set `ADMIN_EMAIL` to an email address you control. Accounts are created or promoted to administrator only after the emailed sign-in link is verified. New accounts otherwise receive the `student` role.

Run the app with:

```powershell
python -m pip install -r requirements.txt
flask --app app init-db
flask --app app run --debug
```

## Roles and permissions

| Role | Permissions |
| --- | --- |
| Student | `quiz.take` |
| Instructor | `quiz.take`, `quiz.create`, `quiz.manage_own` |
| Administrator | All student and instructor permissions, plus `quiz.manage_all`, `users.manage`, and `roles.assign` |

Use `@permission_required("permission.name")` on quiz routes to enforce a permission at the server. The `/admin/users` page is protected by `users.manage`; role assignments also require `roles.assign`. At least one administrator must remain. The Flask-Admin backend is available at `/admin/`, requires `quiz.manage_all`, and provides CRUD management for quizzes, questions (including their inline options), attempts, and answers. Its Users view is separately protected by `users.manage` and role changes also require `roles.assign`; it prevents demoting or deleting the last administrator. Login and pending-login tokens are intentionally not exposed through Flask-Admin. All admin forms use the existing session CSRF token.

Flask-Admin uses a custom Halfmoon UI theme with light, dark, and system appearance options. Its appearance preference is shared with the main app through the `theme` browser setting. Link to the backend with `url_for("quiz_admin.index")`; the admin navigation link is shown only to users with `quiz.manage_all`.

Run `flask --app app init-db` to create tables in a new database. Existing schemas are not migrated automatically; back up any data before applying schema changes.

## Quiz schema

The models define quizzes, ordered questions and options, student attempts, and submitted answers. Database constraints validate supported question/attempt types, positive limits and positions, non-empty content, unique order/attempt records, answer values, and consistency between an answer, its attempt, quiz, question, and selected option.

The basic instructor quiz maker is available at `/instructor/quizzes`: instructors can create a quiz, see their quizzes in a table, edit quiz title/description, and add questions. Multiple-choice options are answer choices for one question: enter one choice per line, then select which numbered choice is correct. True/false questions automatically get True and False choices; short-answer questions do not have choices. Student attempts, publishing, editing/deleting questions, and grading are not implemented yet.

### Registered quiz routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/student/` | Student dashboard |
| GET | `/quizzes` | Browse published quizzes |
| GET | `/quizzes/<quiz_id>` | View quiz details |
| POST | `/quizzes/<quiz_id>/attempts` | Start an attempt |
| GET | `/attempts/<attempt_id>` | Take or resume an attempt |
| POST | `/attempts/<attempt_id>/answers` | Save an answer |
| POST | `/attempts/<attempt_id>/submit` | Submit an attempt |
| GET | `/attempts/<attempt_id>/result` | View an attempt result |
| GET | `/instructor/` | Redirect to instructor quizzes |
| GET | `/instructor/quizzes` | List instructor-owned quizzes |
| GET, POST | `/instructor/quizzes/new` | Create a quiz |
| GET | `/instructor/quizzes/<quiz_id>` | View questions and add a question |
| GET, POST | `/instructor/quizzes/<quiz_id>/edit` | Edit quiz title and description |
| POST | `/instructor/quizzes/<quiz_id>/questions/new` | Add a question and its answer choices |
| GET, POST | `/instructor/quizzes/<quiz_id>/questions/<question_id>/edit` | Edit a question and its options |
| POST | `/instructor/quizzes/<quiz_id>/questions/<question_id>/delete` | Delete a question |
| POST | `/instructor/quizzes/<quiz_id>/publish` | Publish a quiz |
| POST | `/instructor/quizzes/<quiz_id>/delete` | Delete a quiz |
| GET | `/instructor/quizzes/<quiz_id>/attempts` | List quiz attempts |
| GET | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>` | Review an attempt |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/grade` | Grade an attempt |
| GET | `/instructor/quizzes/<quiz_id>/report` | View quiz reporting |
| GET | `/admin/` | Halfmoon-themed Flask-Admin backend |
| GET | `/admin/dashboard` | Administrator dashboard placeholder |
| GET | `/admin/quizzes` | Future quiz oversight placeholder |
| GET | `/admin/quizzes/<quiz_id>` | Future quiz details placeholder |
| GET | `/admin/attempts/<attempt_id>` | Future attempt review placeholder |

Existing authentication, account, and user-management routes remain available. Quiz/student routes require `quiz.take`, instructor authoring routes require `quiz.create` or `quiz.manage_own`, and administrator routes require `quiz.manage_all`. Ownership checks and route behavior will be added with the corresponding workflows.

The Flask-Admin CRUD views are mounted at `/admin/quiz/`, `/admin/question/`, `/admin/attempt/`, and `/admin/answer/`. Question creation/editing includes an ordered inline options editor, so options are managed as part of their question rather than as a separate admin section. The quiz selector and answer-type selector use dropdowns; answer types are shown as “Multiple choice (one correct answer),” “True or false,” and “Short answer.” These views are available only to administrators with `quiz.manage_all`.
