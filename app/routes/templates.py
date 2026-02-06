from flask import Blueprint

bp = Blueprint('templates', __name__, url_prefix='/templates')

@bp.route('/')
def index():
    return "Templates module - Coming in Phase 2"
