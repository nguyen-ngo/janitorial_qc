from flask import Blueprint

bp = Blueprint('inspections', __name__, url_prefix='/inspections')

@bp.route('/')
def index():
    return "Inspections module - Coming in Phase 2"
