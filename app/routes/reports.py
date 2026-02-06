from flask import Blueprint

bp = Blueprint('reports', __name__, url_prefix='/reports')

@bp.route('/')
def index():
    return "Reports module - Coming in Phase 2"
