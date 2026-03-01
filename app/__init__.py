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

    with app.app_context():
        db.create_all()

    return app