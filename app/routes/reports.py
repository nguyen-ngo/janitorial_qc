import csv
import io
from datetime import datetime, timedelta
from app.utils.time_utils import now_eastern
from flask import (Blueprint, render_template, request,
                   Response, stream_with_context)
from flask_login import login_required, current_user
from sqlalchemy import func
from app import db
from app.models.inspection import Inspection, InspectionTemplate
from app.models.facility import Facility, Area
from app.models.issue import Issue
from app.models.user import User
from app.utils.decorators import supervisor_required
from app.utils.scope import get_customer_scope

bp = Blueprint('reports', __name__, url_prefix='/reports')


def _date_range():
    """Parse ?start= and ?end= query params; default to last 30 days."""
    end_default   = now_eastern()
    start_default = end_default - timedelta(days=30)
    try:
        start = datetime.strptime(request.args.get('start', ''), '%Y-%m-%d')
    except ValueError:
        start = start_default
    try:
        end = datetime.strptime(request.args.get('end', ''), '%Y-%m-%d')
        end = end.replace(hour=23, minute=59, second=59)
    except ValueError:
        end = end_default
    return start, end


# ── Overview dashboard ────────────────────────────────────────────────────────

@bp.route('/')
@login_required
def index():
    # Customers get a scoped view; internal staff need supervisor+ access
    if current_user.role not in ['admin', 'supervisor', 'project_manager', 'customer']:
        from flask import flash, redirect, url_for
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))

    start, end = _date_range()

    # Resolve facility scope for customers
    customer_facility_ids = get_customer_scope(current_user)  # None = unrestricted

    def _scope_insp(q):
        if customer_facility_ids is not None:
            if not customer_facility_ids:
                return q.filter(False)
            return q.filter(Inspection.facility_id.in_(customer_facility_ids))
        return q

    def _scope_issue(q):
        if customer_facility_ids is not None:
            if not customer_facility_ids:
                return q.filter(False)
            return q.join(Area, Issue.area_id == Area.id).filter(
                Area.facility_id.in_(customer_facility_ids)
            )
        return q

    base = _scope_insp(Inspection.query.filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
    ))

    total_inspections = base.count()
    completed         = base.filter(Inspection.status == 'completed').count()
    flagged           = _scope_issue(Issue.query.filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
        Issue.status != 'resolved',
    )).count()
    avg_score         = db.session.query(func.avg(Inspection.overall_score)).filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
    )
    avg_score = _scope_insp(avg_score).scalar()

    # Scores by facility (for bar chart)
    fac_score_q = db.session.query(
        Facility.name,
        func.avg(Inspection.overall_score).label('avg_score'),
        func.count(Inspection.id).label('count'),
    ).join(Inspection, Facility.id == Inspection.facility_id)\
     .filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
     )
    if customer_facility_ids is not None:
        fac_score_q = fac_score_q.filter(
            Facility.id.in_(customer_facility_ids) if customer_facility_ids else False
        )
    facility_scores = fac_score_q.group_by(Facility.id, Facility.name)\
                                  .order_by(func.avg(Inspection.overall_score).desc()).all()

    # Score trend — daily averages (line chart)
    daily_q = db.session.query(
        func.date(Inspection.inspection_date).label('day'),
        func.avg(Inspection.overall_score).label('avg'),
        func.count(Inspection.id).label('count'),
    ).filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
    )
    daily_scores = _scope_insp(daily_q).group_by(func.date(Inspection.inspection_date))\
                                        .order_by(func.date(Inspection.inspection_date)).all()

    # Issue breakdown by severity
    issue_severity = _scope_issue(db.session.query(
        Issue.severity,
        func.count(Issue.id).label('count'),
    ).filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    )).group_by(Issue.severity).all()

    # Issue status breakdown
    issue_status = _scope_issue(db.session.query(
        Issue.status,
        func.count(Issue.id).label('count'),
    ).filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    )).group_by(Issue.status).all()

    # Top inspectors by inspection count (hidden for customer role)
    top_inspectors = []
    if current_user.role != 'customer':
        top_inspectors = db.session.query(
            User.username,
            func.count(Inspection.id).label('count'),
            func.avg(Inspection.overall_score).label('avg_score'),
        ).join(Inspection, User.id == Inspection.inspector_id)\
         .filter(
            Inspection.inspection_date >= start,
            Inspection.inspection_date <= end,
            Inspection.status == 'completed',
         ).group_by(User.id, User.username)\
          .order_by(func.count(Inspection.id).desc()).limit(10).all()

    # Recent issues (critical/high) — scoped for customers
    critical_issues = _scope_issue(Issue.query.filter(
        Issue.severity.in_(['critical', 'high']),
        Issue.status != 'resolved',
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    )).order_by(Issue.reported_at.desc()).limit(10).all()

    return render_template('reports/index.html',
        start=start, end=end,
        total_inspections=total_inspections,
        completed=completed,
        flagged=flagged,
        avg_score=round(float(avg_score), 2) if avg_score else None,
        facility_scores=[{'name': r.name, 'avg_score': round(float(r.avg_score), 2), 'count': r.count} for r in facility_scores],
        daily_scores=[{'day': str(r.day), 'avg': round(float(r.avg), 2), 'count': r.count} for r in daily_scores],
        issue_severity=[{'severity': r.severity, 'count': r.count} for r in issue_severity],
        issue_status=[{'status': r.status, 'count': r.count} for r in issue_status],
        top_inspectors=[{'username': r.username, 'count': r.count, 'avg_score': round(float(r.avg_score), 2) if r.avg_score else None} for r in top_inspectors],
        critical_issues=critical_issues,
    )


# ── Facility detail report ────────────────────────────────────────────────────

@bp.route('/facility/<int:facility_id>')
@login_required
def facility_report(facility_id):
    if current_user.role not in ['admin', 'supervisor', 'project_manager', 'customer']:
        from flask import flash, redirect, url_for
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))
    facility    = Facility.query.get_or_404(facility_id)
    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        if facility_id not in cids:
            from flask import flash, redirect, url_for
            flash('Access denied.', 'danger')
            return redirect(url_for('reports.index'))
    start, end  = _date_range()

    inspections = Inspection.query.filter(
        Inspection.facility_id  == facility_id,
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
    ).order_by(Inspection.inspection_date.desc()).all()

    area_scores = db.session.query(
        Area.name,
        func.avg(Inspection.overall_score).label('avg_score'),
        func.count(Inspection.id).label('count'),
    ).join(Inspection, Area.id == Inspection.area_id)\
     .filter(
        Inspection.facility_id == facility_id,
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
     ).group_by(Area.id, Area.name).all()

    open_issues = Issue.query.join(Area)\
        .filter(Area.facility_id == facility_id, Issue.status != 'resolved')\
        .order_by(Issue.severity.desc()).all()

    return render_template('reports/facility.html',
        facility=facility, inspections=inspections,
        area_scores=area_scores, open_issues=open_issues,
        start=start, end=end)



# ── Facility Scorecard ────────────────────────────────────────────────────────

@bp.route('/facility/<int:facility_id>/scorecard')
@login_required
def facility_scorecard(facility_id):
    """Comprehensive per-facility scorecard: score trend, SLA compliance,
    issue breakdown by severity, inspection frequency."""
    if current_user.role not in ['admin', 'supervisor', 'project_manager', 'customer']:
        from flask import flash, redirect, url_for
        flash('Access denied.', 'danger')
        return redirect(url_for('dashboard.index'))

    facility = Facility.query.get_or_404(facility_id)

    if current_user.role == 'customer':
        cids = get_customer_scope(current_user) or []
        if facility_id not in cids:
            from flask import flash, redirect, url_for
            flash('Access denied.', 'danger')
            return redirect(url_for('reports.index'))

    from app.utils.sla import sla_status, SLA_HOURS
    from datetime import timedelta

    now   = now_eastern()
    days  = request.args.get('days', 90, type=int)
    if days not in (30, 60, 90, 180, 365):
        days = 90
    start = now - timedelta(days=days)

    # ── Score trend (daily) ───────────────────────────────────────────────
    trend_rows = db.session.query(
        func.date(Inspection.inspection_date).label('day'),
        func.avg(Inspection.overall_score).label('avg'),
        func.count(Inspection.id).label('count'),
    ).filter(
        Inspection.facility_id    == facility_id,
        Inspection.inspection_date >= start,
        Inspection.status          == 'completed',
        Inspection.overall_score.isnot(None),
    ).group_by(func.date(Inspection.inspection_date))     .order_by(func.date(Inspection.inspection_date)).all()

    trend_labels = [str(r.day) for r in trend_rows]
    trend_data   = [round(float(r.avg), 2) for r in trend_rows]

    # ── KPI summary ───────────────────────────────────────────────────────
    all_insp = Inspection.query.filter(
        Inspection.facility_id    == facility_id,
        Inspection.inspection_date >= start,
    ).all()
    completed_insp = [i for i in all_insp if i.status == 'completed']
    avg_score      = (
        round(sum(float(i.overall_score) for i in completed_insp
                  if i.overall_score is not None)
              / len([i for i in completed_insp if i.overall_score is not None]), 2)
        if any(i.overall_score for i in completed_insp) else None
    )

    # ── Area scores ───────────────────────────────────────────────────────
    area_scores = db.session.query(
        Area.name,
        func.avg(Inspection.overall_score).label('avg'),
        func.count(Inspection.id).label('count'),
    ).join(Inspection, Area.id == Inspection.area_id)     .filter(
        Inspection.facility_id    == facility_id,
        Inspection.inspection_date >= start,
        Inspection.status          == 'completed',
        Inspection.overall_score.isnot(None),
    ).group_by(Area.id, Area.name)     .order_by(func.avg(Inspection.overall_score).desc()).all()

    # ── Open issues ───────────────────────────────────────────────────────
    open_issues = Issue.query.join(Area)        .filter(Area.facility_id == facility_id, Issue.status != 'resolved')        .order_by(Issue.reported_at.desc()).all()

    # SLA compliance for closed issues in window
    closed_issues = Issue.query.join(Area).filter(
        Area.facility_id == facility_id,
        Issue.status     == 'resolved',
        Issue.reported_at >= start,
    ).all()
    sla_met     = sum(1 for i in closed_issues
                      if i.resolved_at and i.reported_at
                      and (i.resolved_at - i.reported_at).total_seconds() / 3600
                         <= SLA_HOURS.get(i.severity, 9999))
    sla_total   = len(closed_issues)
    sla_pct     = round(sla_met / sla_total * 100, 1) if sla_total else None

    # Issue severity breakdown
    sev_counts = {}
    for sev in ('critical', 'high', 'medium', 'low'):
        sev_counts[sev] = Issue.query.join(Area).filter(
            Area.facility_id == facility_id,
            Issue.severity   == sev,
            Issue.status     != 'resolved',
        ).count()

    # Pending verification count
    pending_verification = Issue.query.join(Area).filter(
        Area.facility_id == facility_id,
        Issue.status     == 'pending_verification',
    ).count()

    # Follow-up required inspections
    followup_required = Inspection.query.filter(
        Inspection.facility_id     == facility_id,
        Inspection.follow_up_required == True,
    ).order_by(Inspection.inspection_date.desc()).limit(10).all()

    return render_template('reports/scorecard.html',
        facility             = facility,
        days                 = days,
        start                = start,
        now                  = now,
        total_inspections    = len(all_insp),
        completed_insp       = len(completed_insp),
        avg_score            = avg_score,
        trend_labels         = trend_labels,
        trend_data           = trend_data,
        area_scores          = area_scores,
        open_issues          = open_issues,
        sla_pct              = sla_pct,
        sla_met              = sla_met,
        sla_total            = sla_total,
        sev_counts           = sev_counts,
        pending_verification = pending_verification,
        followup_required    = followup_required,
    )

# ── CSV export ────────────────────────────────────────────────────────────────

@bp.route('/export/inspections')
@login_required
@supervisor_required
def export_inspections():
    start, end = _date_range()

    rows = db.session.query(
        Inspection.id,
        Inspection.inspection_date,
        Facility.name.label('facility'),
        Area.name.label('area'),
        User.username.label('inspector'),
        InspectionTemplate.name.label('template'),
        Inspection.overall_score,
        Inspection.status,
        Inspection.completed_at,
        Inspection.notes,
    ).join(Facility, Inspection.facility_id == Facility.id)\
     .outerjoin(Area,    Inspection.area_id    == Area.id)\
     .join(User,         Inspection.inspector_id == User.id)\
     .join(InspectionTemplate, Inspection.template_id == InspectionTemplate.id)\
     .filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
     ).order_by(Inspection.inspection_date.desc()).all()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['ID','Date','Facility','Area','Inspector','Template',
                         'Score','Status','Completed At','Notes'])
        yield buf.getvalue(); buf.seek(0); buf.truncate()

        for r in rows:
            writer.writerow([
                r.id,
                r.inspection_date.strftime('%Y-%m-%d %H:%M') if r.inspection_date else '',
                r.facility, r.area or '',
                r.inspector, r.template,
                r.overall_score or '',
                r.status,
                r.completed_at.strftime('%Y-%m-%d %H:%M') if r.completed_at else '',
                (r.notes or '').replace('\n', ' '),
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate()

    filename = f"inspections_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return Response(
        stream_with_context(generate()),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


@bp.route('/export/issues')
@login_required
@supervisor_required
def export_issues():
    start, end = _date_range()

    rows = db.session.query(
        Issue.id,
        Issue.reported_at,
        Facility.name.label('facility'),
        Area.name.label('area'),
        Issue.severity,
        Issue.description,
        Issue.status,
        Issue.resolved_at,
        User.username.label('assigned_to'),
    ).join(Area, Issue.area_id == Area.id)\
     .join(Facility, Area.facility_id == Facility.id)\
     .outerjoin(User, Issue.assigned_to == User.id)\
     .filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
     ).order_by(Issue.reported_at.desc()).all()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(['ID','Reported At','Facility','Area','Severity',
                         'Description','Status','Resolved At','Assigned To'])
        yield buf.getvalue(); buf.seek(0); buf.truncate()

        for r in rows:
            writer.writerow([
                r.id,
                r.reported_at.strftime('%Y-%m-%d %H:%M') if r.reported_at else '',
                r.facility, r.area, r.severity,
                r.description.replace('\n', ' '),
                r.status,
                r.resolved_at.strftime('%Y-%m-%d %H:%M') if r.resolved_at else '',
                r.assigned_to or '',
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate()

    filename = f"issues_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
    return Response(
        stream_with_context(generate()),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )