"""
app/routes/scheduled_reports.py
--------------------------------
CRUD management for ScheduledReport configs + cron-triggered send endpoint.

Admin/supervisor access for management.
The /send route is token-protected (same DIGEST_SECRET) for cron use.

Cron examples
-------------
# Daily at 07:00
0 7 * * * curl -s -X POST https://yourdomain.com/scheduled-reports/send \
    -d "token=YOUR_DIGEST_SECRET&frequency=daily"

# Weekly on Monday 07:00
0 7 * * 1 curl -s -X POST https://yourdomain.com/scheduled-reports/send \
    -d "token=YOUR_DIGEST_SECRET&frequency=weekly"

# Monthly on the 1st at 07:00
0 7 1 * * curl -s -X POST https://yourdomain.com/scheduled-reports/send \
    -d "token=YOUR_DIGEST_SECRET&frequency=monthly"
"""

import csv
import io
import logging
from datetime import datetime, timedelta

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, jsonify, current_app)
from flask_login import login_required, current_user
from flask_mail import Message
from sqlalchemy import func

from app import db, mail, csrf
from app.models.scheduled_report import ScheduledReport
from app.models.inspection import Inspection, InspectionTemplate
from app.models.facility import Facility, Area
from app.models.issue import Issue
from app.models.user import User
from app.utils.decorators import supervisor_required
from app.utils.audit import log_action, ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE
from app.utils.time_utils import now_eastern

logger = logging.getLogger(__name__)

bp = Blueprint('scheduled_reports', __name__, url_prefix='/scheduled-reports')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compute_next_send(frequency: str, from_dt: datetime = None) -> datetime:
    """Return the next send datetime for a given frequency."""
    now = from_dt or now_eastern()
    if frequency == 'daily':
        return (now + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    if frequency == 'weekly':
        return (now + timedelta(weeks=1)).replace(hour=7, minute=0, second=0, microsecond=0)
    # monthly: first of next month
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=7, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=7, minute=0, second=0, microsecond=0)


def _date_window(frequency: str):
    """Return (start, end) covering the period just elapsed for this frequency."""
    end   = now_eastern()
    if frequency == 'daily':
        start = end - timedelta(days=1)
    elif frequency == 'weekly':
        start = end - timedelta(weeks=1)
    else:
        start = end - timedelta(days=30)
    return start, end


def _build_report_data(report: ScheduledReport, start: datetime, end: datetime) -> dict:
    """Assemble the data dict passed to the email template."""
    data = {
        'report':     report,
        'start':      start,
        'end':        end,
        'facility':   report.facility,
    }

    fid_filter = [report.facility_id] if report.facility_id else None

    def _si(q):
        if fid_filter:
            return q.filter(Inspection.facility_id.in_(fid_filter))
        return q

    def _iq(q):
        if fid_filter:
            return q.join(Area, Issue.area_id == Area.id).filter(Area.facility_id.in_(fid_filter))
        return q

    if report.report_type in ('summary', 'facility'):
        base = _si(Inspection.query.filter(
            Inspection.inspection_date >= start,
            Inspection.inspection_date <= end,
        ))
        data['total_inspections'] = base.count()
        data['completed']         = base.filter(Inspection.status == 'completed').count()

        avg = db.session.query(func.avg(Inspection.overall_score)).filter(
            Inspection.inspection_date >= start,
            Inspection.inspection_date <= end,
            Inspection.status == 'completed',
            Inspection.overall_score.isnot(None),
        )
        avg_val = _si(avg).scalar()
        data['avg_score'] = round(float(avg_val), 2) if avg_val else None

        data['open_issues'] = _iq(Issue.query.filter(
            Issue.status.in_(['open', 'in_progress'])
        )).count()

        data['critical_issues'] = _iq(Issue.query.filter(
            Issue.severity.in_(['critical', 'high']),
            Issue.status != 'resolved',
        )).order_by(Issue.reported_at.desc()).limit(10).all()

        data['facility_scores'] = db.session.query(
            Facility.name,
            func.avg(Inspection.overall_score).label('avg'),
            func.count(Inspection.id).label('count'),
        ).join(Inspection, Facility.id == Inspection.facility_id).filter(
            Inspection.inspection_date >= start,
            Inspection.inspection_date <= end,
            Inspection.status == 'completed',
            Inspection.overall_score.isnot(None),
        )
        if fid_filter:
            data['facility_scores'] = data['facility_scores'].filter(Facility.id.in_(fid_filter))
        data['facility_scores'] = data['facility_scores'].group_by(Facility.id, Facility.name)\
                                                          .order_by(func.avg(Inspection.overall_score).desc()).all()

    if report.report_type == 'issues':
        data['issues'] = _iq(Issue.query.filter(
            Issue.status != 'resolved',
        )).order_by(Issue.severity.desc(), Issue.reported_at.asc()).all()

    return data


def _build_csv(report: ScheduledReport, start: datetime, end: datetime) -> bytes:
    """Return CSV bytes appropriate for the report type."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    fid_filter = [report.facility_id] if report.facility_id else None

    if report.report_type == 'issues':
        writer.writerow(['ID', 'Reported At', 'Facility', 'Area', 'Severity',
                         'Description', 'Status', 'Assigned To'])
        q = Issue.query
        if fid_filter:
            q = q.join(Area, Issue.area_id == Area.id).filter(Area.facility_id.in_(fid_filter))
        for i in q.filter(Issue.status != 'resolved').order_by(Issue.reported_at.desc()).all():
            writer.writerow([
                i.id,
                i.reported_at.strftime('%Y-%m-%d %H:%M'),
                i.area.facility.name,
                i.area.name,
                i.severity,
                i.description.replace('\n', ' '),
                i.status,
                i.assigned_user.username if i.assigned_user else '',
            ])
    else:
        writer.writerow(['ID', 'Date', 'Facility', 'Area', 'Inspector',
                         'Template', 'Score', 'Status'])
        q = Inspection.query.filter(
            Inspection.inspection_date >= start,
            Inspection.inspection_date <= end,
        )
        if fid_filter:
            q = q.filter(Inspection.facility_id.in_(fid_filter))
        for i in q.order_by(Inspection.inspection_date.desc()).all():
            writer.writerow([
                i.id,
                i.inspection_date.strftime('%Y-%m-%d %H:%M'),
                i.facility.name,
                i.area.name if i.area else '',
                i.inspector.username,
                i.template.name,
                i.overall_score or '',
                i.status,
            ])

    return buf.getvalue().encode('utf-8')


def _send_report(report: ScheduledReport):
    """Build and dispatch the email for a single ScheduledReport."""
    if not current_app.config.get('MAIL_SERVER'):
        logger.warning('SCHEDULED REPORT SKIPPED | id=%s | no MAIL_SERVER', report.id)
        return False

    recipients = report.recipient_list()
    if not recipients:
        logger.warning('SCHEDULED REPORT SKIPPED | id=%s | no recipients', report.id)
        return False

    frequency  = report.frequency
    start, end = _date_window(frequency)
    data       = _build_report_data(report, start, end)
    base_url   = current_app.config.get('APP_BASE_URL', '').rstrip('/')

    html_body = render_template('scheduled_reports/email.html',
                                base_url=base_url, **data)
    text_body = render_template('scheduled_reports/email.txt',
                                base_url=base_url, **data)

    subject = (f'[JQC] {report.frequency.title()} Report — {report.name} '
               f'({start.strftime("%b %d")}–{end.strftime("%b %d, %Y")})')

    sender = current_app.config.get(
        'MAIL_DEFAULT_SENDER',
        current_app.config.get('MAIL_USERNAME', 'noreply@janitorialqc.local'),
    )

    msg = Message(subject=subject, sender=sender, recipients=recipients,
                  body=text_body, html=html_body)

    if report.include_csv:
        csv_bytes = _build_csv(report, start, end)
        fname     = f'jqc_report_{report.frequency}_{start.strftime("%Y%m%d")}.csv'
        msg.attach(fname, 'text/csv', csv_bytes)

    if report.include_pdf:
        try:
            from app.utils.pdf_export import generate_scheduled_report_pdf
            pdf_bytes = generate_scheduled_report_pdf(
                report_name   = report.name,
                frequency     = report.frequency,
                start         = start,
                end           = end,
                facility_name = report.facility.name if report.facility else None,
                data          = data,
            )
            pdf_fname = f'jqc_report_{report.frequency}_{start.strftime("%Y%m%d")}.pdf'
            msg.attach(pdf_fname, 'application/pdf', pdf_bytes)
        except Exception as exc:
            logger.error('SCHEDULED REPORT PDF FAILED | id=%s | error=%s', report.id, exc)

    try:
        mail.send(msg)
        logger.info('SCHEDULED REPORT SENT | id=%s | name=%r | recipients=%s',
                    report.id, report.name, recipients)
        return True
    except Exception as exc:
        logger.error('SCHEDULED REPORT FAILED | id=%s | error=%s', report.id, exc)
        return False


# ── CRUD ──────────────────────────────────────────────────────────────────────

@bp.route('/')
@login_required
@supervisor_required
def index():
    reports = ScheduledReport.query.order_by(ScheduledReport.name).all()
    return render_template('scheduled_reports/index.html', reports=reports)


@bp.route('/new', methods=['GET', 'POST'])
@login_required
@supervisor_required
def create():
    facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    if request.method == 'POST':
        name        = request.form.get('name', '').strip()
        report_type = request.form.get('report_type', 'summary')
        frequency   = request.form.get('frequency', 'weekly')
        facility_id = request.form.get('facility_id', type=int) or None
        recipients  = [e.strip() for e in request.form.get('recipients', '').split(',') if e.strip()]
        include_pdf = bool(request.form.get('include_pdf'))
        include_csv = bool(request.form.get('include_csv'))

        if not name:
            flash('Report name is required.', 'warning')
            return render_template('scheduled_reports/form.html',
                                   facilities=facilities, title='New Scheduled Report')
        if not recipients:
            flash('At least one recipient email is required.', 'warning')
            return render_template('scheduled_reports/form.html',
                                   facilities=facilities, title='New Scheduled Report')

        report = ScheduledReport(
            name        = name,
            report_type = report_type,
            frequency   = frequency,
            facility_id = facility_id,
            recipients  = recipients,
            include_pdf = include_pdf,
            include_csv = include_csv,
            active      = True,
            created_by  = current_user.id,
            created_at  = now_eastern(),
            next_send_at = _compute_next_send(frequency),
        )
        db.session.add(report)
        db.session.commit()
        log_action(ACTION_CREATE, 'ScheduledReport', report.id, report.name,
                   f'frequency={frequency}; recipients={len(recipients)}')
        flash(f'Scheduled report "{report.name}" created.', 'success')
        return redirect(url_for('scheduled_reports.index'))

    return render_template('scheduled_reports/form.html',
                           facilities=facilities, title='New Scheduled Report')


@bp.route('/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
@supervisor_required
def edit(report_id):
    report     = ScheduledReport.query.get_or_404(report_id)
    facilities = Facility.query.filter_by(active=True).order_by(Facility.name).all()

    if request.method == 'POST':
        report.name        = request.form.get('name', '').strip() or report.name
        report.report_type = request.form.get('report_type', report.report_type)
        report.frequency   = request.form.get('frequency', report.frequency)
        report.facility_id = request.form.get('facility_id', type=int) or None
        report.recipients  = [e.strip() for e in request.form.get('recipients', '').split(',') if e.strip()]
        report.include_pdf = bool(request.form.get('include_pdf'))
        report.include_csv = bool(request.form.get('include_csv'))
        report.active      = bool(request.form.get('active'))
        report.next_send_at = _compute_next_send(report.frequency)

        db.session.commit()
        log_action(ACTION_UPDATE, 'ScheduledReport', report.id, report.name,
                   f'frequency={report.frequency}; active={report.active}')
        flash(f'Scheduled report "{report.name}" updated.', 'success')
        return redirect(url_for('scheduled_reports.index'))

    return render_template('scheduled_reports/form.html', report=report,
                           facilities=facilities, title='Edit Scheduled Report')


@bp.route('/<int:report_id>/delete', methods=['POST'])
@login_required
@supervisor_required
def delete(report_id):
    report = ScheduledReport.query.get_or_404(report_id)
    name   = report.name
    rid    = report.id
    db.session.delete(report)
    db.session.commit()
    log_action(ACTION_DELETE, 'ScheduledReport', rid, name)
    flash(f'Scheduled report "{name}" deleted.', 'success')
    return redirect(url_for('scheduled_reports.index'))


@bp.route('/<int:report_id>/preview')
@login_required
@supervisor_required
def preview(report_id):
    """Render the scheduled report email in-browser for review."""
    report     = ScheduledReport.query.get_or_404(report_id)
    start, end = _date_window(report.frequency)
    data       = _build_report_data(report, start, end)
    base_url   = current_app.config.get('APP_BASE_URL', '').rstrip('/')

    current_app.logger.info(
        'SCHEDULED REPORT PREVIEW | id=%s | name=%r | by=%s',
        report.id, report.name, current_user.username,
    )

    return render_template('scheduled_reports/email.html',
                           base_url=base_url, **data)


@bp.route('/<int:report_id>/preview-pdf')
@login_required
@supervisor_required
def preview_pdf(report_id):
    """Generate and stream the PDF attachment for in-browser review."""
    from flask import Response
    from app.utils.pdf_export import generate_scheduled_report_pdf

    report     = ScheduledReport.query.get_or_404(report_id)
    start, end = _date_window(report.frequency)
    data       = _build_report_data(report, start, end)

    pdf_bytes = generate_scheduled_report_pdf(
        report_name   = report.name,
        frequency     = report.frequency,
        start         = start,
        end           = end,
        facility_name = report.facility.name if report.facility else None,
        data          = data,
    )

    filename = f'jqc_report_{report.frequency}_{start.strftime("%Y%m%d")}.pdf'

    current_app.logger.info(
        'SCHEDULED REPORT PDF PREVIEW | id=%s | name=%r | by=%s',
        report.id, report.name, current_user.username,
    )

    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'inline; filename="{filename}"'},
    )


@bp.route('/<int:report_id>/send-now', methods=['POST'])
@login_required
@supervisor_required
def send_now(report_id):
    """Manually trigger a single report — useful for testing."""
    report = ScheduledReport.query.get_or_404(report_id)
    ok = _send_report(report)
    if ok:
        report.last_sent_at = now_eastern()
        db.session.commit()
        log_action(ACTION_UPDATE, 'ScheduledReport', report.id, report.name,
                   f'manual send_now by {current_user.username}; '
                   f'frequency={report.frequency}; recipients={len(report.recipient_list())}')
        flash(f'Report "{report.name}" sent successfully.', 'success')
    else:
        flash(f'Failed to send report "{report.name}". Check application logs.', 'danger')
    return redirect(url_for('scheduled_reports.index'))


# ── Cron endpoint ─────────────────────────────────────────────────────────────

@bp.route('/send', methods=['POST'])
@csrf.exempt
def send():
    """Token-protected endpoint called by cron to dispatch due reports.

    POST body: token=<DIGEST_SECRET>&frequency=daily|weekly|monthly
    """
    token     = request.form.get('token') or request.args.get('token')
    frequency = request.form.get('frequency', 'daily')
    expected  = current_app.config.get('DIGEST_SECRET')

    if not expected or token != expected:
        logger.warning('SCHEDULED REPORTS SEND REJECTED | bad/missing token')
        return jsonify({'ok': False, 'error': 'unauthorized'}), 403

    if frequency not in ('daily', 'weekly', 'monthly'):
        return jsonify({'ok': False, 'error': 'invalid frequency'}), 400

    now     = now_eastern()
    reports = ScheduledReport.query.filter_by(active=True, frequency=frequency).all()
    # Only send reports whose next_send_at is due (or not yet set)
    due     = [r for r in reports if r.next_send_at is None or r.next_send_at <= now]

    sent, failed = 0, 0
    for report in due:
        ok = _send_report(report)
        # Always advance next_send_at so a failed report does not get
        # retried on every subsequent cron run.  last_sent_at is only
        # updated on a successful delivery so the UI accurately reflects
        # when the last good email was dispatched.
        report.next_send_at = _compute_next_send(frequency, now)
        if ok:
            report.last_sent_at = now
            sent += 1
        else:
            failed += 1
            logger.error(
                'SCHEDULED REPORT FAILED | id=%s | name=%r | frequency=%s',
                report.id, report.name, frequency,
            )
    db.session.commit()

    logger.info('SCHEDULED REPORTS CRON | frequency=%s | due=%s | sent=%s | failed=%s',
                frequency, len(due), sent, failed)
    return jsonify({'ok': True, 'frequency': frequency, 'sent': sent, 'failed': failed})