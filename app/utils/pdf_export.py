"""
pdf_export.py
-------------
Generates a professional PDF report for a completed inspection.

Uses ReportLab Platypus (flow-based layout) so the document repaginates
cleanly regardless of how many form fields or issues exist.

Public API
----------
    generate_inspection_pdf(inspection, form_fields, form_data, issues,
                            static_folder) -> bytes
"""

import io
import os
import json
from datetime import datetime

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage
)

# ── Colour palette (matches the web UI) ──────────────────────────────────────
C_DARK   = colors.HexColor('#1a1d23')
C_BLUE   = colors.HexColor('#2563eb')
C_SLATE  = colors.HexColor('#64748b')
C_LIGHT  = colors.HexColor('#f1f5f9')
C_BORDER = colors.HexColor('#e2e8f0')
C_GREEN  = colors.HexColor('#16a34a')
C_YELLOW = colors.HexColor('#d97706')
C_RED    = colors.HexColor('#dc2626')
C_WHITE  = colors.white

SEVERITY_COLORS = {
    'critical': C_RED,
    'high':     C_RED,
    'medium':   C_YELLOW,
    'low':      C_SLATE,
}

STATUS_COLORS = {
    'completed': C_GREEN,
    'flagged':   C_RED,
    'in_progress': C_YELLOW,
}


# ── Style sheet ───────────────────────────────────────────────────────────────

def _build_styles():
    base = getSampleStyleSheet()

    def add(name, parent='Normal', **kw):
        base.add(ParagraphStyle(name=name, parent=base[parent], **kw))

    add('ReportTitle',  parent='Normal',
        fontSize=18, textColor=C_WHITE, fontName='Helvetica-Bold',
        spaceAfter=2)
    add('ReportSub',    parent='Normal',
        fontSize=9,  textColor=colors.HexColor('#94a3b8'),
        fontName='Helvetica', spaceAfter=0)
    add('SectionHead',  parent='Normal',
        fontSize=10, textColor=C_DARK, fontName='Helvetica-Bold',
        spaceBefore=8, spaceAfter=4)
    add('FieldLabel',   parent='Normal',
        fontSize=7.5, textColor=C_SLATE, fontName='Helvetica',
        spaceAfter=1)
    add('FieldValue',   parent='Normal',
        fontSize=8.5, textColor=C_DARK,  fontName='Helvetica',
        spaceAfter=2)
    add('MetaLabel',    parent='Normal',
        fontSize=7, textColor=C_SLATE, fontName='Helvetica',
        spaceAfter=0)
    add('MetaValue',    parent='Normal',
        fontSize=9, textColor=C_DARK,  fontName='Helvetica-Bold',
        spaceAfter=0)
    add('IssueDesc',    parent='Normal',
        fontSize=8, textColor=C_DARK, fontName='Helvetica',
        spaceAfter=2)
    add('FooterStyle',  parent='Normal',
        fontSize=7, textColor=C_SLATE, fontName='Helvetica',
        alignment=TA_CENTER)

    return base


STYLES = _build_styles()


# ── Header / footer callbacks ─────────────────────────────────────────────────

def _on_page(canvas, doc, title, generated_at):
    """Draw page header band and footer on every page."""
    w, h = letter
    margin = 0.65 * inch

    # ── Dark header band ──
    canvas.saveState()
    canvas.setFillColor(C_DARK)
    canvas.rect(0, h - 1.1 * inch, w, 1.1 * inch, stroke=0, fill=1)

    canvas.setFont('Helvetica-Bold', 13)
    canvas.setFillColor(C_WHITE)
    canvas.drawString(margin, h - 0.55 * inch, title)

    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#94a3b8'))
    canvas.drawString(margin, h - 0.78 * inch, 'Janitorial Quality Control System')

    # Page number — right-aligned
    page_txt = f'Page {doc.page}'
    canvas.setFont('Helvetica', 8)
    canvas.setFillColor(colors.HexColor('#94a3b8'))
    canvas.drawRightString(w - margin, h - 0.66 * inch, page_txt)
    canvas.restoreState()

    # ── Footer ──
    canvas.saveState()
    canvas.setStrokeColor(C_BORDER)
    canvas.line(margin, 0.55 * inch, w - margin, 0.55 * inch)
    canvas.setFont('Helvetica', 7)
    canvas.setFillColor(C_SLATE)
    canvas.drawString(margin, 0.35 * inch,
                      f'Generated: {generated_at}   |   Janitorial QC System')
    canvas.drawRightString(w - margin, 0.35 * inch, 'CONFIDENTIAL')
    canvas.restoreState()


# ── Score badge helper ────────────────────────────────────────────────────────

def _score_color(score):
    if score is None:
        return C_SLATE
    s = float(score)
    if s >= 90:
        return C_GREEN
    if s >= 70:
        return C_YELLOW
    return C_RED


# ── Meta info table ───────────────────────────────────────────────────────────

def _meta_table(inspection):
    def cell(label, value):
        return [
            Paragraph(label, STYLES['MetaLabel']),
            Paragraph(str(value), STYLES['MetaValue']),
        ]

    date_str = inspection.inspection_date.strftime('%B %d, %Y')
    time_str = inspection.inspection_date.strftime('%I:%M %p ET')
    completed = (inspection.completed_at.strftime('%B %d, %Y  %I:%M %p ET')
                 if inspection.completed_at else '—')

    score_val = (f'{float(inspection.overall_score):.1f}%'
                 if inspection.overall_score is not None else '—')

    status_txt = inspection.status.replace('_', ' ').title()

    area_txt = inspection.area.name if inspection.area else '—'

    data = [
        [cell('INSPECTOR',    inspection.inspector.username),
         cell('DATE',         date_str),
         cell('TIME',         time_str)],
        [cell('FACILITY',     inspection.facility.name),
         cell('AREA',         area_txt),
         cell('COMPLETED',    completed)],
        [cell('TEMPLATE',     inspection.template.name),
         cell('FREQUENCY',    inspection.template.frequency.title()),
         cell('STATUS / SCORE', f'{status_txt}   {score_val}')],
    ]

    # Flatten each inner list into a single-column cell using a mini Table
    rows = []
    for row in data:
        rows.append([Table([[item] for item in pair],
                           colWidths=[1.5 * inch],
                           style=TableStyle([
                               ('VALIGN',   (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING',  (0, 0), (-1, -1), 0),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING',   (0, 0), (-1, -1), 1),
                               ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
                           ]))
               for pair in row]

    col_w = (letter[0] - 1.3 * inch) / 3
    tbl = Table(rows, colWidths=[col_w] * 3)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), C_LIGHT),
        ('BOX',        (0, 0), (-1, -1), 0.5, C_BORDER),
        ('INNERGRID',  (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING',   (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
    ]))
    return tbl


# ── Score banner ──────────────────────────────────────────────────────────────

def _score_banner(inspection):
    score = inspection.overall_score
    score_txt = f'{float(score):.1f}%' if score is not None else 'N/A'
    sc = _score_color(score)

    grade = 'PASS' if (score is not None and float(score) >= 70) else 'FAIL'
    grade_color = C_GREEN if grade == 'PASS' else C_RED

    data = [[
        Paragraph(f'<font color="white"><b>Overall Score</b></font>',
                  ParagraphStyle('x', fontName='Helvetica-Bold', fontSize=9,
                                 textColor=C_WHITE, alignment=TA_CENTER)),
        Paragraph(f'<font color="white"><b>{score_txt}</b></font>',
                  ParagraphStyle('x2', fontName='Helvetica-Bold', fontSize=22,
                                 textColor=C_WHITE, alignment=TA_CENTER)),
        Paragraph(f'<font color="white"><b>{grade}</b></font>',
                  ParagraphStyle('x3', fontName='Helvetica-Bold', fontSize=14,
                                 textColor=C_WHITE, alignment=TA_CENTER)),
    ]]

    w = letter[0] - 1.3 * inch
    tbl = Table(data, colWidths=[w * 0.35, w * 0.35, w * 0.30])
    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (1, 0), sc),
        ('BACKGROUND',   (2, 0), (2, 0), grade_color),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING',   (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 10),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    return tbl


# ── Form fields section ───────────────────────────────────────────────────────

def _star_string(score, max_stars=5):
    filled = min(int(score), max_stars)
    return '★' * filled + '☆' * (max_stars - filled)


_SKIP_TYPES = {'label', 'section', 'button_submit', 'button_print', 'button_email'}


def _form_fields_section(form_fields, form_data, static_folder):
    """Render all answered form fields as a two-column table per row."""
    story = []
    story.append(Paragraph('Inspection Results', STYLES['SectionHead']))
    story.append(HRFlowable(width='100%', thickness=1, color=C_BORDER,
                             spaceAfter=6))

    current_section = None
    rows = []           # list of [label_para, value_para] pairs

    def _flush_rows():
        if not rows:
            return []
        out = []
        w = letter[0] - 1.3 * inch
        for label_p, value_p in rows:
            tbl = Table([[label_p, value_p]],
                        colWidths=[w * 0.38, w * 0.62])
            tbl.setStyle(TableStyle([
                ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING',  (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('TOPPADDING',   (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
                ('LINEBELOW',    (0, 0), (-1, 0), 0.25, C_BORDER),
            ]))
            out.append(tbl)
        rows.clear()
        return out

    for field in form_fields:
        ftype = field.get('type', '')
        fid   = str(field.get('id', ''))
        val   = form_data.get(fid, '')

        if ftype == 'section':
            story.extend(_flush_rows())
            story.append(Spacer(1, 6))
            story.append(Paragraph(field.get('label', ''),
                                   ParagraphStyle('SecH', fontName='Helvetica-Bold',
                                                  fontSize=9.5, textColor=C_BLUE,
                                                  spaceBefore=6, spaceAfter=2,
                                                  borderPad=3,
                                                  backColor=colors.HexColor('#eff6ff'))))
            story.append(HRFlowable(width='100%', thickness=0.5,
                                     color=C_BLUE, spaceAfter=4))
            continue

        if ftype in _SKIP_TYPES:
            continue

        label = field.get('label', fid)
        label_p = Paragraph(label, STYLES['FieldLabel'])

        # ── Format value by type ──
        if ftype == 'rating':
            score_int = int(val) if str(val).isdigit() else 0
            if score_int > 0:
                val_text = f'{_star_string(score_int)}  ({score_int}/5)'
            else:
                val_text = '<i><font color="#94a3b8">Not rated</font></i>'
            value_p = Paragraph(val_text, STYLES['FieldValue'])

        elif ftype == 'checkbox':
            val_text = ('Yes' if val == 'yes' else 'No')
            value_p  = Paragraph(val_text, STYLES['FieldValue'])

        elif ftype == 'checkbox_group':
            if val and isinstance(val, list):
                val_text = ',  '.join(val)
            else:
                val_text = '<i><font color="#94a3b8">None selected</font></i>'
            value_p = Paragraph(val_text, STYLES['FieldValue'])

        elif ftype == 'image':
            if val:
                img_path = os.path.join(static_folder, val)
                if os.path.exists(img_path):
                    try:
                        max_w, max_h = 2.5 * inch, 1.8 * inch
                        img = RLImage(img_path, width=max_w, height=max_h,
                                      kind='proportional')
                        rows.append([label_p, img])
                        continue
                    except Exception:
                        pass
            value_p = Paragraph('<i><font color="#94a3b8">No photo</font></i>',
                                STYLES['FieldValue'])

        elif ftype == 'signature':
            # Base64 signatures can't be embedded inline easily — note presence only
            if val and str(val).startswith('data:'):
                val_text = '[Signature captured]'
            else:
                val_text = '<i><font color="#94a3b8">No signature</font></i>'
            value_p = Paragraph(val_text, STYLES['FieldValue'])

        elif ftype == 'table':
            if val and isinstance(val, list) and len(val) > 0:
                headers = field.get('col_headers', list(val[0].keys()) if val else [])
                tbl_data = [headers] + [[row.get(h, '') for h in headers] for row in val]
                inner = Table(tbl_data)
                inner.setStyle(TableStyle([
                    ('BACKGROUND',   (0, 0), (-1, 0),  C_LIGHT),
                    ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
                    ('FONTSIZE',     (0, 0), (-1, -1), 7),
                    ('INNERGRID',    (0, 0), (-1, -1), 0.25, C_BORDER),
                    ('BOX',          (0, 0), (-1, -1), 0.5,  C_BORDER),
                    ('TOPPADDING',   (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
                ]))
                rows.append([label_p, inner])
                continue
            value_p = Paragraph('<i><font color="#94a3b8">No data</font></i>',
                                STYLES['FieldValue'])

        else:
            # text, textarea, number, date, email, radio, select
            disp = str(val) if val else '—'
            value_p = Paragraph(disp, STYLES['FieldValue'])

        if not val and ftype not in ('rating',):
            # Skip entirely empty non-rated fields to keep the PDF concise
            continue

        rows.append([label_p, value_p])

    story.extend(_flush_rows())
    return story


# ── Issues section ────────────────────────────────────────────────────────────

def _issues_section(issues):
    if not issues:
        return []

    story = [
        Spacer(1, 10),
        Paragraph('Flagged Issues', STYLES['SectionHead']),
        HRFlowable(width='100%', thickness=1, color=C_RED, spaceAfter=6),
    ]

    headers = ['#', 'Severity', 'Area', 'Description', 'Status']
    col_w   = [0.3 * inch, 0.7 * inch, 1.1 * inch, 3.5 * inch, 0.9 * inch]

    rows = [headers]
    for i, issue in enumerate(issues, 1):
        desc = issue.description[:120] + ('…' if len(issue.description) > 120 else '')
        rows.append([
            str(i),
            issue.severity.title(),
            issue.area.name,
            desc,
            issue.status.replace('_', ' ').title(),
        ])

    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    sev_styles = []
    for r, issue in enumerate(issues, 1):
        sc = SEVERITY_COLORS.get(issue.severity, C_SLATE)
        sev_styles.append(('TEXTCOLOR', (1, r), (1, r), sc))
        sev_styles.append(('FONTNAME',  (1, r), (1, r), 'Helvetica-Bold'))

    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  C_DARK),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  C_WHITE),
        ('FONTNAME',     (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',     (0, 0), (-1, -1), 8),
        ('INNERGRID',    (0, 0), (-1, -1), 0.25, C_BORDER),
        ('BOX',          (0, 0), (-1, -1), 0.5,  C_BORDER),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [C_WHITE, C_LIGHT]),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        *sev_styles,
    ]))
    story.append(tbl)
    return story


# ── Notes section ─────────────────────────────────────────────────────────────

def _notes_section(inspection):
    notes_text = None
    if inspection.notes:
        try:
            parsed = json.loads(inspection.notes)
            if isinstance(parsed, dict):
                notes_text = parsed.get('_inspector_notes') or parsed.get('notes')
        except (json.JSONDecodeError, TypeError):
            notes_text = inspection.notes

    if not notes_text:
        return []

    return [
        Spacer(1, 10),
        Paragraph('Inspector Notes', STYLES['SectionHead']),
        HRFlowable(width='100%', thickness=1, color=C_BORDER, spaceAfter=6),
        Paragraph(str(notes_text),
                  ParagraphStyle('Notes', fontName='Helvetica', fontSize=8.5,
                                 textColor=C_DARK, leading=13,
                                 backColor=colors.HexColor('#fffbeb'),
                                 borderPad=6, spaceAfter=6)),
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def generate_inspection_pdf(inspection, form_fields, form_data, issues,
                             static_folder) -> bytes:
    """
    Build and return a PDF byte-string for the given inspection.

    Parameters
    ----------
    inspection   : Inspection model instance
    form_fields  : list of field dicts from template.get_form_schema()
    form_data    : dict of {field_id: value}
    issues       : list of Issue model instances
    static_folder: absolute path to app/static (for resolving photo paths)
    """
    buf = io.BytesIO()
    generated_at = datetime.now().strftime('%B %d, %Y  %I:%M %p ET')
    report_title = f'Inspection Report — {inspection.template.name}'

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.65 * inch,
        rightMargin=0.65 * inch,
        topMargin=1.25 * inch,      # leave room for the header band
        bottomMargin=0.75 * inch,
        title=report_title,
        author='Janitorial QC System',
    )

    def _page_cb(canvas, doc):
        _on_page(canvas, doc, report_title, generated_at)

    story = []

    # ── Score banner ──
    story.append(_score_banner(inspection))
    story.append(Spacer(1, 8))

    # ── Meta table ──
    story.append(_meta_table(inspection))
    story.append(Spacer(1, 12))

    # ── Form fields ──
    story.extend(_form_fields_section(form_fields, form_data, static_folder))

    # ── Inspector notes ──
    story.extend(_notes_section(inspection))

    # ── Issues ──
    story.extend(_issues_section(issues))

    # ── Signature line ──
    story.append(Spacer(1, 24))
    w = letter[0] - 1.3 * inch
    sig_data = [['Inspector Signature', '', 'Date']]
    sig_tbl = Table(sig_data, colWidths=[w * 0.45, w * 0.1, w * 0.45])
    sig_tbl.setStyle(TableStyle([
        ('LINEABOVE',    (0, 0), (0, 0), 0.75, C_DARK),
        ('LINEABOVE',    (2, 0), (2, 0), 0.75, C_DARK),
        ('FONTNAME',     (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE',     (0, 0), (-1, -1), 8),
        ('TEXTCOLOR',    (0, 0), (-1, -1), C_SLATE),
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    story.append(sig_tbl)

    doc.build(story, onFirstPage=_page_cb, onLaterPages=_page_cb)
    return buf.getvalue()
