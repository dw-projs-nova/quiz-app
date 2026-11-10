from flask import current_app
from sqlalchemy import inspect, text

from models import db


def initialize_database():
    db.create_all()
    removed_tokens = migrate_schema()
    if removed_tokens:
        current_app.logger.warning(
            "Removed %s orphaned login token record(s) during database migration.",
            removed_tokens,
        )


def migrate_schema():
    with db.engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("quiz_attempt_locks"):
            columns = {
                column["name"]
                for column in inspector.get_columns("quiz_attempt_locks")
            }
            if "reason" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE quiz_attempt_locks "
                        "ADD COLUMN reason VARCHAR(20) "
                        "NOT NULL DEFAULT 'instructor'"
                    )
                )
        result = connection.execute(
            text(
                "DELETE FROM login_tokens "
                "WHERE NOT EXISTS ("
                "SELECT 1 FROM users WHERE users.id = login_tokens.user_id"
                ")"
            )
        )
        return result.rowcount
