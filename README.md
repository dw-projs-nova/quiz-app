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

Use `@permission_required("permission.name")` on routes to enforce permissions at the server. All administrator UI is provided by Flask-Admin at `/admin/`; its backend requires `quiz.manage_all`, while its Users view is separately protected by `users.manage`. Role changes also require `roles.assign`, and the Users view prevents demoting or deleting the last administrator. Login and pending-login tokens are not exposed through Flask-Admin. All admin forms use the existing session CSRF token.

Flask-Admin uses a custom Halfmoon UI theme with light, dark, and system appearance options. Its appearance preference is shared with the main app through the `theme` browser setting. Link to the backend with `url_for("quiz_admin.index")`; the admin navigation link is shown only to users with `quiz.manage_all`.

Run `flask --app app init-db` to create tables in a new database and apply the app's additive SQLite schema migrations. The app also applies these migrations at startup. Existing records are preserved; the current migration adds the lock-reason column and removes unusable login-token rows whose users no longer exist. SQLite foreign-key enforcement is enabled for new connections. Back up the database before upgrading.

## Quiz schema

The models define quizzes, quiz settings, instructor-owned classes, student enrollments, class quiz assignments, ordered questions and options, accepted short-answer keys, student attempts, and submitted answers. These features use additive tables, so `create_all()` creates them in existing databases without altering quiz tables.

The instructor workspace is available at `/instructor/`, `/instructor/quizzes`, and `/instructor/classes`. Teachers create classes and share generated class codes; students join at `/classes`. A quiz can be assigned to an entire class or to selected students from either the quiz page or class roster. Individual assignments do not enroll a student in a class; class assignments give access to enrolled students. Students see only published quizzes assigned to them or to one of their classes. Teachers can set a quiz password, opening and closing times, a time limit, an attempt limit, and one of three student layouts: Classic cards, Focus mode, or Compact list. Exam mode hides app navigation, allows full screen, and pauses the attempt timer whenever the student exits full screen; the timer resumes when full screen is restored. If the exam tab loses focus, the app saves answers and locks the attempt until an instructor unlocks it. The timer displays hours, minutes, and seconds as needed; the server enforces the deadline. A blank attempt limit allows unlimited retakes.

Teachers can organize quiz questions into ordered sections. Sections are shown as headings in the student quiz, and questions can be assigned to a section while creating or editing them. Question and section changes are available while a quiz is unpublished and no attempts are in progress.

The instructor workspace also includes a dashboard, quiz search/status filters, question creation and editing with a live preview, publish/unpublish controls, student attempt review, grading, and question-level reports. From the attempts page, teachers can grant an individual student an additional attempt or invalidate an attempt. Invalidated attempts remain available for instructor review but no longer count toward the student's attempt limit or quiz reports. Multiple-choice questions have one correct answer; true/false questions are generated automatically. Short-answer questions can include accepted responses (one per line) for automatic exact matching that ignores capitalization and repeated whitespace. Short answers without an answer key require instructor grading. Questions can be edited while a quiz is unpublished and has no active attempts; questions with recorded responses cannot be deleted. Quizzes with attempts cannot be deleted. Student answers are automatically saved while typing. In exam mode, choosing “Save progress” saves answers and locks that attempt until an instructor unlocks it; outside exam mode it saves without locking.

### Registered quiz routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/student/` | Student dashboard and recent attempts |
| GET | `/quizzes` | Browse published quizzes |
| GET | `/quizzes/<quiz_id>` | View assigned quiz details, availability, and attempt limits |
| POST | `/quizzes/<quiz_id>/attempts` | Start or resume an assigned quiz attempt |
| GET | `/classes` | View joined classes and enter a class code |
| POST | `/classes/join` | Join a class with its code |
| GET | `/attempts/<attempt_id>` | Take or resume an attempt |
| POST | `/attempts/<attempt_id>/answers` | Save all answers in progress |
| POST | `/attempts/<attempt_id>/pause` | Save answers and pause an exam-mode attempt outside full screen |
| POST | `/attempts/<attempt_id>/resume` | Resume a paused exam-mode attempt in full screen |
| POST | `/attempts/<attempt_id>/focus-lost` | Save answers and lock an exam-mode attempt after tab focus is lost |
| POST | `/attempts/<attempt_id>/submit` | Submit and auto-grade an attempt |
| GET | `/attempts/<attempt_id>/result` | View an attempt score or review status |
| GET | `/instructor/` | Instructor overview dashboard |
| GET | `/instructor/quizzes` | List instructor-owned quizzes |
| GET, POST | `/instructor/classes` | List classes and create a class code |
| GET, POST | `/instructor/classes/<class_id>` | Manage class roster and quiz assignments |
| GET, POST | `/instructor/quizzes/new` | Create a quiz |
| GET | `/instructor/quizzes/<quiz_id>` | View questions and add a question |
| POST | `/instructor/quizzes/<quiz_id>/assignments` | Assign/remove quiz access for a class or an individual student |
| GET, POST | `/instructor/quizzes/<quiz_id>/edit` | Edit quiz details and attempt settings |
| POST | `/instructor/quizzes/<quiz_id>/questions/new` | Add a question and its answer choices |
| GET, POST | `/instructor/quizzes/<quiz_id>/questions/<question_id>/edit` | Edit a question and its options before attempts exist |
| POST | `/instructor/quizzes/<quiz_id>/questions/<question_id>/delete` | Delete a question |
| POST | `/instructor/quizzes/<quiz_id>/questions/<question_id>/move` | Reorder a question before attempts exist |
| POST | `/instructor/quizzes/<quiz_id>/sections` | Add a quiz section before attempts exist |
| POST | `/instructor/quizzes/<quiz_id>/sections/<section_id>/delete` | Remove a section before attempts exist |
| POST | `/instructor/quizzes/<quiz_id>/publish` | Publish or unpublish a quiz |
| POST | `/instructor/quizzes/<quiz_id>/delete` | Delete a quiz with no attempts |
| GET | `/instructor/quizzes/<quiz_id>/attempts` | List quiz attempts |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/lock` | Lock one active student attempt |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/unlock` | Unlock one active student attempt |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/grant` | Grant that student one additional attempt |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/invalidate` | Invalidate an attempt while retaining it for instructor review |
| POST | `/instructor/quizzes/<quiz_id>/attempts/control` | Lock/unlock all active attempts, end, or reopen a quiz |
| GET | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>` | Review an attempt |
| POST | `/instructor/quizzes/<quiz_id>/attempts/<attempt_id>/grade` | Save an attempt grade |
| GET | `/instructor/quizzes/<quiz_id>/report` | View quiz reporting |
| GET | `/admin/` | Halfmoon-themed Flask-Admin backend |
Quiz/student routes require `quiz.take`, instructor authoring and class-management routes require instructor permissions, and Flask-Admin routes check the relevant administrator permission. Instructor quiz, class, and attempt routes check ownership; administrators with `quiz.manage_all` can manage all quizzes through Flask-Admin.

Flask-Admin provides CRUD views for quizzes, questions and their inline options/accepted answers, quiz availability and theme, exam mode, sections, question-section assignments, quiz closures, attempts, answers, attempt timers/pauses/locks, classes, enrollments, and class or individual quiz assignments. Related records are selected from dropdowns where the models define relationships. Quiz passwords can be set or cleared in the availability and theme view; password hashes and login tokens are never displayed. Question answer types use a dropdown with “Multiple choice (one correct answer),” “True or false,” and “Short answer.” These views are available only to administrators with `quiz.manage_all`.
