import csv
import io
from datetime import datetime, timedelta
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

bp = Blueprint('reports', __name__, url_prefix='/reports')


def _date_range():
    """Parse ?start= and ?end= query params; default to last 30 days."""
    end_default   = datetime.utcnow()
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
@supervisor_required
def index():
    start, end = _date_range()

    base = Inspection.query.filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
    )

    total_inspections = base.count()
    completed         = base.filter(Inspection.status == 'completed').count()
    flagged           = base.filter(Inspection.status == 'flagged').count()
    avg_score         = db.session.query(func.avg(Inspection.overall_score)).filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
    ).scalar()

    # Scores by facility (for bar chart)
    facility_scores = db.session.query(
        Facility.name,
        func.avg(Inspection.overall_score).label('avg_score'),
        func.count(Inspection.id).label('count'),
    ).join(Inspection, Facility.id == Inspection.facility_id)\
     .filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
     ).group_by(Facility.id, Facility.name)\
      .order_by(func.avg(Inspection.overall_score).desc()).all()

    # Score trend — daily averages (line chart)
    daily_scores = db.session.query(
        func.date(Inspection.inspection_date).label('day'),
        func.avg(Inspection.overall_score).label('avg'),
        func.count(Inspection.id).label('count'),
    ).filter(
        Inspection.inspection_date >= start,
        Inspection.inspection_date <= end,
        Inspection.status == 'completed',
        Inspection.overall_score.isnot(None),
    ).group_by(func.date(Inspection.inspection_date))\
     .order_by(func.date(Inspection.inspection_date)).all()

    # Issue breakdown by severity
    issue_severity = db.session.query(
        Issue.severity,
        func.count(Issue.id).label('count'),
    ).filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    ).group_by(Issue.severity).all()

    # Issue status breakdown
    issue_status = db.session.query(
        Issue.status,
        func.count(Issue.id).label('count'),
    ).filter(
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    ).group_by(Issue.status).all()

    # Top inspectors by inspection count
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

    # Recent issues (critical/high)
    critical_issues = Issue.query.filter(
        Issue.severity.in_(['critical', 'high']),
        Issue.status != 'resolved',
        Issue.reported_at >= start,
        Issue.reported_at <= end,
    ).order_by(Issue.reported_at.desc()).limit(10).all()

    return render_template('reports/index.html',
        start=start, end=end,
        total_inspections=total_inspections,
        completed=completed,
        flagged=flagged,
        avg_score=round(float(avg_score), 2) if avg_score else None,
        facility_scores=facility_scores,
        daily_scores=daily_scores,
        issue_severity=issue_severity,
        issue_status=issue_status,
        top_inspectors=top_inspectors,
        critical_issues=critical_issues,
    )


# ── Facility detail report ────────────────────────────────────────────────────

@bp.route('/facility/<int:facility_id>')
@login_required
@supervisor_required
def facility_report(facility_id):
    facility    = Facility.query.get_or_404(facility_id)
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
