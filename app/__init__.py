from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_mail import Mail
from config import config
import os

db           = SQLAlchemy()
login_manager = LoginManager()
migrate      = Migrate()
mail         = Mail()


def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])

    db.init_app(app)
    login_manager.init_app(app)
    migrate.init_app(app, db)
    mail.init_app(app)

    login_manager.login_view         = 'auth.login'
    login_manager.login_message      = 'Please log in to access this page.'
    login_manager.login_message_category = 'info'

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    from app.routes import auth, dashboard, inspections, templates, reports, facilities
    from app.routes import issues  # Phase 3

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(inspections.bp)
    app.register_blueprint(templates.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(facilities.bp)
    app.register_blueprint(issues.bp)

    with app.app_context():
        db.create_all()

    return app
