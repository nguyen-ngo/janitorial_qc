from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app.models.inspection import Inspection, InspectionTemplate
from app.models.facility import Facility
from app.models.issue import Issue
from app.models.user import User
from sqlalchemy import func
from datetime import datetime, timedelta

bp = Blueprint('dashboard', __name__)

@bp.route('/')
@bp.route('/dashboard')
@login_required
def index():
    # Get today's date range
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    
    # Statistics for today
    if current_user.role == 'inspector':
        today_inspections = Inspection.query.filter(
            Inspection.inspector_id == current_user.id,
            Inspection.inspection_date >= today_start,
            Inspection.inspection_date < today_end
        ).count()
        
        completed_today = Inspection.query.filter(
            Inspection.inspector_id == current_user.id,
            Inspection.status == 'completed',
            Inspection.inspection_date >= today_start,
            Inspection.inspection_date < today_end
        ).count()
        
        open_issues = Issue.query.join(Inspection).filter(
            Inspection.inspector_id == current_user.id,
            Issue.status.in_(['open', 'in_progress'])
        ).count()
        
    else:
        today_inspections = Inspection.query.filter(
            Inspection.inspection_date >= today_start,
            Inspection.inspection_date < today_end
        ).count()
        
        completed_today = Inspection.query.filter(
            Inspection.status == 'completed',
            Inspection.inspection_date >= today_start,
            Inspection.inspection_date < today_end
        ).count()
        
        open_issues = Issue.query.filter(
            Issue.status.in_(['open', 'in_progress'])
        ).count()
    
    # Calculate average score (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    avg_score_query = Inspection.query.filter(
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
        Inspection.inspection_date >= thirty_days_ago
    )
    
    if current_user.role == 'inspector':
        avg_score_query = avg_score_query.filter(Inspection.inspector_id == current_user.id)
    
    avg_score = db.session.query(func.avg(Inspection.overall_score)).filter(
        Inspection.id.in_([i.id for i in avg_score_query.all()])
    ).scalar()
    
    # Recent inspections
    recent_inspections_query = Inspection.query.order_by(Inspection.inspection_date.desc()).limit(5)
    if current_user.role == 'inspector':
        recent_inspections_query = recent_inspections_query.filter(Inspection.inspector_id == current_user.id)
    
    recent_inspections = recent_inspections_query.all()
    
    # System statistics (admin/supervisor only)
    total_facilities = Facility.query.filter_by(active=True).count() if current_user.role in ['admin', 'supervisor'] else 0
    total_templates = InspectionTemplate.query.count() if current_user.role in ['admin', 'supervisor'] else 0
    total_users = User.query.count() if current_user.role == 'admin' else 0
    
    return render_template('dashboard.html',
        today_inspections=today_inspections,
        completed_today=completed_today,
        open_issues=open_issues,
        avg_score=round(avg_score, 2) if avg_score else None,
        recent_inspections=recent_inspections,
        total_facilities=total_facilities,
        total_templates=total_templates,
        total_users=total_users
    )