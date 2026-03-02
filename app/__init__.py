from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_mail import Mail
from config import config
import os
import logging
from logging.handlers import RotatingFileHandler

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

    # ── Logging setup ────────────────────────────────────────────────────────
    # Configure root logger so that logger.info/error calls in all modules
    # (notifications.py, issues.py, etc.) actually write output.
    # Writes to stdout (captured by journalctl/gunicorn) AND a rotating file.
    if not app.debug or os.environ.get('LOG_TO_FILE'):
        log_level = logging.INFO

        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        # Stream handler — always on; journalctl captures stdout
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(log_level)
        stream_handler.setFormatter(formatter)

        # Rotating file handler — keeps 5 × 5 MB log files
        log_dir  = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, 'jqc.log'),
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)

        # Apply to both the Flask app logger and the root logger so all
        # getLogger(__name__) calls in sub-modules are captured.
        app.logger.setLevel(log_level)
        app.logger.addHandler(stream_handler)
        app.logger.addHandler(file_handler)

        root_logger = logging.getLogger()
        root_logger.setLevel(log_level)
        if not root_logger.handlers:
            root_logger.addHandler(stream_handler)
            root_logger.addHandler(file_handler)

    # Register csrf_token() as an app-wide Jinja2 global so templates that
    # render manual forms (no WTForms object) can still inject the CSRF token.
    from flask_wtf.csrf import generate_csrf
    app.jinja_env.globals['csrf_token'] = generate_csrf
    app.jinja_env.globals['enumerate']  = enumerate

    # ── Inject unread notification count into every template context ──────
    # This powers the red badge on the navbar bell icon without requiring
    # individual routes to pass the count manually.
    from flask_login import current_user

    @app.context_processor
    def inject_notification_count():
        try:
            if current_user.is_authenticated:
                from app.models.notification import Notification
                count = Notification.query.filter_by(
                    user_id=current_user.id, is_read=False
                ).count()
                return {'unread_notification_count': count}
        except Exception:
            pass
        return {'unread_notification_count': 0}

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    from app.routes import auth, dashboard, inspections, templates, reports, facilities
    from app.routes import issues          # Phase 3
    from app.routes import notifications   # Notification system

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(inspections.bp)
    app.register_blueprint(templates.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(facilities.bp)
    app.register_blueprint(issues.bp)
    app.register_blueprint(notifications.bp)

    # ── Error handler: 413 Request Entity Too Large ───────────────────────
    # Nginx can return 413 before Flask sees the request; this handler covers
    # the Flask-side rejection and gives users a clear, actionable message
    # with a redirect back into the inspection workflow.
    from werkzeug.exceptions import RequestEntityTooLarge

    @app.errorhandler(RequestEntityTooLarge)
    @app.errorhandler(413)
    def handle_413(e):
        from flask import request as flask_request, flash as flask_flash, redirect, url_for
        flask_flash(
            f'The uploaded file(s) are too large. '
            f'Please reduce the photo size or upload fewer photos at once '
            f'(maximum {app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)}MB per submission).',
            'danger'
        )
        # Redirect back to the referring page if available, otherwise dashboard
        referrer = flask_request.referrer
        return redirect(referrer or url_for('dashboard.index')), 302

    return app