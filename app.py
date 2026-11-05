from dotenv import load_dotenv

load_dotenv()

from flask import Flask

from config import Config
from controllers import main
from controllers.admin import init_admin
from models import db


def create_app(config_overrides=None):
    app = Flask(__name__, template_folder="Templates", static_folder="Static")
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    app.register_blueprint(main)
    init_admin(app)

    with app.app_context():
        db.create_all()

    @app.cli.command("init-db")
    def init_db():
        db.create_all()
        print("Quiz App database tables are ready.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"])
