from flask import Blueprint, render_template
from flask_login import login_required, current_user
from app import db
from app.models.inspection import Inspection, InspectionTemplate
from app.models.facility import Facility
from app.models.issue import Issue
from app.models.user import User
from app.utils.sla import sla_status, SLA_HOURS
from app.utils.scope import get_customer_scope
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

    is_inspector      = current_user.role == 'inspector'
    is_privileged     = current_user.role in ['admin', 'director']
    is_customer       = current_user.role == 'customer'
    is_project_manager = current_user.role == 'project_manager'

    # Resolve facility scope for customer users
    customer_facility_ids = get_customer_scope(current_user)  # None for non-customers

    # ── Today's stats ─────────────────────────────────────────────────────
    base_q = Inspection.query
    if is_inspector:
        base_q = base_q.filter(Inspection.inspector_id == current_user.id)
    elif is_customer:
        if not customer_facility_ids:
            base_q = base_q.filter(False)  # no access
        else:
            base_q = base_q.filter(Inspection.facility_id.in_(customer_facility_ids))

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
    elif is_customer:
        if not customer_facility_ids:
            open_issues_q = open_issues_q.filter(False)
        else:
            from app.models.facility import Area
            open_issues_q = open_issues_q.join(
                Area, Issue.area_id == Area.id
            ).filter(Area.facility_id.in_(customer_facility_ids))
    open_issues = open_issues_q.count()

    # ── Average score (last 30 days) ───────────────────────────────────────
    score_q = db.session.query(func.avg(Inspection.overall_score)).filter(
        Inspection.status          == 'completed',
        Inspection.overall_score.isnot(None),
        Inspection.inspection_date >= thirty_days_ago,
    )
    if is_inspector:
        score_q = score_q.filter(Inspection.inspector_id == current_user.id)
    elif is_customer:
        if customer_facility_ids:
            score_q = score_q.filter(Inspection.facility_id.in_(customer_facility_ids))
        else:
            score_q = score_q.filter(False)
    avg_score = score_q.scalar()

    # ── Recent inspections ─────────────────────────────────────────────────
    recent_q = Inspection.query.order_by(Inspection.inspection_date.desc())
    if is_inspector:
        recent_q = recent_q.filter(Inspection.inspector_id == current_user.id)
    elif is_customer:
        if customer_facility_ids:
            recent_q = recent_q.filter(Inspection.facility_id.in_(customer_facility_ids))
        else:
            recent_q = recent_q.filter(False)
    recent_inspections = recent_q.limit(5).all()

    # ── Pending follow-up inspections ────────────────────────────────────
    followup_q = Inspection.query.filter_by(
        follow_up_required=True, status='completed'
    ).filter(Inspection.follow_ups == None)  # noqa: E711 — SQLAlchemy usage
    if is_inspector:
        followup_q = followup_q.filter(Inspection.inspector_id == current_user.id)
    elif is_customer:
        if customer_facility_ids:
            followup_q = followup_q.filter(Inspection.facility_id.in_(customer_facility_ids))
        else:
            followup_q = followup_q.filter(False)
    pending_followups = followup_q.count()

    # ── System stats (admin/director) ────────────────────────────────────────
    total_facilities = Facility.query.filter_by(active=True).count() if is_privileged else 0
    total_templates  = InspectionTemplate.query.count()               if is_privileged else 0
    total_users      = User.query.count()                             if current_user.role == 'admin' else 0

    # ── Customer: scoped facilities summary ───────────────────────────────
    customer_facilities = []
    if is_customer and customer_facility_ids:
        customer_facilities = Facility.query.filter(
            Facility.id.in_(customer_facility_ids),
            Facility.active == True,
        ).order_by(Facility.name).all()

    # ── SLA summary (open + in_progress issues only) ──────────────────────
    sla_q = Issue.query.filter(Issue.status.in_(['open', 'in_progress']))
    if is_customer and customer_facility_ids:
        from app.models.facility import Area
        sla_q = sla_q.join(Area, Issue.area_id == Area.id).filter(
            Area.facility_id.in_(customer_facility_ids)
        )
    all_open_issues = sla_q.all() if not is_customer or customer_facility_ids else []
    sla_breached    = sum(1 for i in all_open_issues if sla_status(i) == 'breached')
    sla_at_risk     = sum(1 for i in all_open_issues if sla_status(i) == 'at_risk')

    # ── Score trend (last 30 days, grouped by day) ────────────────────────
    trend_q = (
        db.session.query(
            func.date(Inspection.inspection_date).label('day'),
            func.avg(Inspection.overall_score).label('avg'),
        )
        .filter(
            Inspection.status          == 'completed',
            Inspection.overall_score.isnot(None),
            Inspection.inspection_date >= thirty_days_ago,
        )
    )
    if is_inspector:
        trend_q = trend_q.filter(Inspection.inspector_id == current_user.id)
    elif is_customer:
        if customer_facility_ids:
            trend_q = trend_q.filter(Inspection.facility_id.in_(customer_facility_ids))
        else:
            trend_q = trend_q.filter(False)
    trend_rows   = trend_q.group_by(func.date(Inspection.inspection_date))\
                          .order_by(func.date(Inspection.inspection_date)).all()
    trend_labels = [str(r.day) for r in trend_rows]
    trend_data   = [round(float(r.avg), 2) for r in trend_rows]

    # ── Facility performance (last 30 days, privileged users only) ─────────
    facility_perf = []
    if is_privileged or is_project_manager:
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

    # ── Facilities list for the trend-by-facility chart selector ────────────
    if is_privileged or is_project_manager:
        all_facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()
    elif is_customer and customer_facility_ids:
        all_facilities = Facility.query.filter(
            Facility.id.in_(customer_facility_ids), Facility.active == True
        ).order_by(Facility.name).all()
    else:
        all_facilities = []

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
        customer_facilities = customer_facilities,
        pending_followups   = pending_followups,
        all_facilities      = all_facilities,
    )


# ── AJAX: facility score trend ────────────────────────────────────────────────

@bp.route('/facility-trend')
@login_required
def facility_trend():
    """Return daily avg-score data for a single facility over N days.

    Query params:
        facility_id  (int, required)
        days         (int, default 30 — allowed: 30, 60, 90)

    Response JSON:
        { labels: ['2026-03-01', ...], data: [85.2, ...], facility: 'Name' }
    """
    from flask import jsonify, request as req

    facility_id = req.args.get('facility_id', type=int)
    days        = req.args.get('days', 30, type=int)
    if days not in (30, 60, 90):
        days = 30

    if not facility_id:
        return jsonify({'labels': [], 'data': [], 'facility': ''})

    # Scope check for customer users
    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        if facility_id not in cids:
            return jsonify({'labels': [], 'data': [], 'facility': ''}), 403

    facility = db.session.get(Facility, facility_id)
    if not facility:
        return jsonify({'labels': [], 'data': [], 'facility': ''})

    start = now_eastern() - timedelta(days=days)

    rows = (
        db.session.query(
            func.date(Inspection.inspection_date).label('day'),
            func.avg(Inspection.overall_score).label('avg'),
        )
        .filter(
            Inspection.facility_id     == facility_id,
            Inspection.status          == 'completed',
            Inspection.overall_score.isnot(None),
            Inspection.inspection_date >= start,
        )
        .group_by(func.date(Inspection.inspection_date))
        .order_by(func.date(Inspection.inspection_date))
        .all()
    )

    return jsonify({
        'labels':   [str(r.day) for r in rows],
        'data':     [round(float(r.avg), 2) for r in rows],
        'facility': facility.name,
    })