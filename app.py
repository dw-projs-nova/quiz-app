from dotenv import load_dotenv

load_dotenv()

from flask import Flask
from sqlalchemy import event

from config import Config
from controllers import main
from controllers.admin import init_admin
from db_migrations import initialize_database
from models import db


def enable_sqlite_foreign_keys(engine):
    if engine.dialect.name != "sqlite":
        return

    def set_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(engine, "connect", set_foreign_keys)


def create_app(config_overrides=None):
    app = Flask(__name__, template_folder="Templates", static_folder="Static")
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    with app.app_context():
        enable_sqlite_foreign_keys(db.engine)

    app.register_blueprint(main)
    init_admin(app)

    with app.app_context():
        initialize_database()

    @app.cli.command("init-db")
    def init_db():
        initialize_database()
        print("Quiz App database tables and schema are ready.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
