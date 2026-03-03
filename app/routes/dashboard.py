from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app import db
from app.models.inspection import Inspection, InspectionTemplate
from app.models.facility import Facility
from app.models.issue import Issue
from app.models.user import User
from app.utils.sla import sla_status, SLA_HOURS
from sqlalchemy import func
from datetime import datetime, timedelta
from app.utils.time_utils import now_eastern

bp = Blueprint('dashboard', __name__)


@bp.route('/')
@bp.route('/dashboard')
@login_required
def index():
    now         = now_eastern()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)
    thirty_days_ago = now - timedelta(days=30)

    is_inspector  = current_user.role == 'inspector'
    is_privileged = current_user.role in ['admin', 'supervisor']

    # ── Today's stats ─────────────────────────────────────────────────────
    base_q = Inspection.query
    if is_inspector:
        base_q = base_q.filter(Inspection.inspector_id == current_user.id)

    today_inspections = base_q.filter(
        Inspection.inspection_date >= today_start,
        Inspection.inspection_date <  today_end,
    ).count()

    completed_today = base_q.filter(
        Inspection.status          == 'completed',
        Inspection.inspection_date >= today_start,
        Inspection.inspection_date <  today_end,
    ).count()

    # ── Open issues ────────────────────────────────────────────────────────
    open_issues_q = Issue.query.filter(Issue.status.in_(['open', 'in_progress']))
    if is_inspector:
        open_issues_q = open_issues_q.join(
            Inspection, Issue.inspection_id == Inspection.id
        ).filter(Inspection.inspector_id == current_user.id)
    open_issues = open_issues_q.count()

    # ── Average score (last 30 days) ───────────────────────────────────────
    score_q = db.session.query(func.avg(Inspection.overall_score)).filter(
        Inspection.status          == 'completed',
        Inspection.overall_score.isnot(None),
        Inspection.inspection_date >= thirty_days_ago,
    )
    if is_inspector:
        score_q = score_q.filter(Inspection.inspector_id == current_user.id)
    avg_score = score_q.scalar()

    # ── Recent inspections ─────────────────────────────────────────────────
    recent_q = Inspection.query.order_by(Inspection.inspection_date.desc())
    if is_inspector:
        recent_q = recent_q.filter(Inspection.inspector_id == current_user.id)
    recent_inspections = recent_q.limit(5).all()

    # ── System stats (admin/supervisor) ───────────────────────────────────
    total_facilities = Facility.query.filter_by(active=True).count() if is_privileged else 0
    total_templates  = InspectionTemplate.query.count()               if is_privileged else 0
    total_users      = User.query.count()                             if current_user.role == 'admin' else 0

    # ── SLA summary (open + in_progress issues only) ──────────────────────
    all_open_issues = Issue.query.filter(Issue.status.in_(['open', 'in_progress'])).all()
    sla_breached    = sum(1 for i in all_open_issues if sla_status(i) == 'breached')
    sla_at_risk     = sum(1 for i in all_open_issues if sla_status(i) == 'at_risk')

    # ── Score trend (last 30 days, grouped by day) ────────────────────────
    trend_rows = (
        db.session.query(
            func.date(Inspection.inspection_date).label('day'),
            func.avg(Inspection.overall_score).label('avg'),
        )
        .filter(
            Inspection.status          == 'completed',
            Inspection.overall_score.isnot(None),
            Inspection.inspection_date >= thirty_days_ago,
        )
        .group_by(func.date(Inspection.inspection_date))
        .order_by(func.date(Inspection.inspection_date))
        .all()
    )
    trend_labels = [str(r.day) for r in trend_rows]
    trend_data   = [round(float(r.avg), 2) for r in trend_rows]

    # ── Facility performance (last 30 days, privileged users only) ─────────
    facility_perf = []
    if is_privileged:
        perf_rows = (
            db.session.query(
                Facility.name,
                func.count(Inspection.id).label('count'),
                func.avg(Inspection.overall_score).label('avg'),
            )
            .join(Inspection, Inspection.facility_id == Facility.id)
            .filter(
                Inspection.status          == 'completed',
                Inspection.overall_score.isnot(None),
                Inspection.inspection_date >= thirty_days_ago,
                Facility.active            == True,
            )
            .group_by(Facility.id, Facility.name)
            .order_by(func.avg(Inspection.overall_score).desc())
            .all()
        )
        facility_perf = [
            {'name': r.name, 'count': r.count, 'avg': round(float(r.avg), 1)}
            for r in perf_rows
        ]

    return render_template(
        'dashboard.html',
        today_inspections  = today_inspections,
        completed_today    = completed_today,
        open_issues        = open_issues,
        avg_score          = round(avg_score, 2) if avg_score else None,
        recent_inspections = recent_inspections,
        total_facilities   = total_facilities,
        total_templates    = total_templates,
        total_users        = total_users,
        sla_breached       = sla_breached,
        sla_at_risk        = sla_at_risk,
        trend_labels       = trend_labels,
        trend_data         = trend_data,
        facility_perf      = facility_perf,
    )
