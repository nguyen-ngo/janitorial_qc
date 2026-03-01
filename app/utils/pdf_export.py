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
    """Render a 6-column label/value grid covering the key inspection metadata."""
    date_str   = inspection.inspection_date.strftime('%B %d, %Y')
    time_str   = inspection.inspection_date.strftime('%I:%M %p ET')
    completed  = (inspection.completed_at.strftime('%B %d, %Y  %I:%M %p ET')
                  if inspection.completed_at else '—')
    score_val  = (f'{float(inspection.overall_score):.1f}%'
                  if inspection.overall_score is not None else '—')
    status_txt = inspection.status.replace('_', ' ').title()
    area_txt   = inspection.area.name if inspection.area else '—'

    def lbl(text):
        return Paragraph(text, STYLES['MetaLabel'])

    def val(text):
        return Paragraph(str(text), STYLES['MetaValue'])

    # Each row: alternating label / value columns (6 cols total)
    rows = [
        [lbl('INSPECTOR'),   val(inspection.inspector.username),
         lbl('DATE'),        val(date_str),
         lbl('TIME'),        val(time_str)],
        [lbl('FACILITY'),    val(inspection.facility.name),
         lbl('AREA'),        val(area_txt),
         lbl('COMPLETED'),   val(completed)],
        [lbl('TEMPLATE'),    val(inspection.template.name),
         lbl('FREQUENCY'),   val(inspection.template.frequency.title()),
         lbl('STATUS / SCORE'), val(f'{status_txt}   {score_val}')],
    ]

    col_w = (letter[0] - 1.3 * inch) / 6
    tbl = Table(rows, colWidths=[col_w] * 6)
    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), C_LIGHT),
        ('BOX',          (0, 0), (-1, -1), 0.5, C_BORDER),
        ('INNERGRID',    (0, 0), (-1, -1), 0.5, C_BORDER),
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING',   (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
        # Label rows get a lighter text treatment (already in STYLES['MetaLabel'])
        ('TEXTCOLOR',    (0, 0), (-1, -1), C_DARK),
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
    """Return a star string using only the filled ★ glyph.
    Hollow ☆ is not supported by Helvetica and renders as a box.
    Unselected stars are rendered as grey ★ via ReportLab XML markup.
    Returns a plain string for canvas use; use _star_markup() for Paragraph.
    """
    filled = min(int(score), max_stars)
    return '★' * filled + '★' * (max_stars - filled)


_SKIP_TYPES = {'button_submit', 'button_print', 'button_email'}
_GRID_COLS   = 12   # matches the web UI 12-column grid


def _form_fields_section(form_fields, form_data, static_folder):
    """
    Reconstruct the web UI grid layout in PDF form.

    Fields are grouped by their grid row number.  Within each row the fields
    are placed side-by-side with column widths proportional to their colSpan.
    Section headings (type='section') are rendered as full-width banners
    between groups of rows, exactly as they appear in the web UI.
    """
    story = []
    story.append(Paragraph('Inspection Results', STYLES['SectionHead']))
    story.append(HRFlowable(width='100%', thickness=1, color=C_BORDER, spaceAfter=6))

    FULL_W = letter[0] - 1.3 * inch   # usable page width
    UNIT   = FULL_W / _GRID_COLS       # width of one grid column unit

    # ── Group fields by row number, preserving original order ────────────────
    from collections import defaultdict, OrderedDict
    rows_map = OrderedDict()   # row_num -> [field, ...]
    sections = {}              # row_num -> section label that precedes this row

    pending_section = None
    for field in form_fields:
        ftype = field.get('type', '')
        if ftype in ('button_submit', 'button_print', 'button_email'):
            continue
        if ftype == 'section':
            pending_section = field.get('label', '')
            continue

        row_num = field.get('row', 0)
        if row_num not in rows_map:
            rows_map[row_num] = []
            if pending_section is not None:
                sections[row_num] = pending_section
                pending_section = None

        rows_map[row_num].append(field)

    # ── Pre-compute which rows have at least one visible field ───────────────
    # A row is visible if it contains a text/textarea/label field, OR any other
    # field type that has a non-empty value.  This is used to suppress section
    # banners whose every subordinate row is empty.
    _ALWAYS_SHOW_PRE = {'label'}

    def _row_has_content(fields_in_row):
        for f in fields_in_row:
            ft  = f.get('type', '')
            fid = str(f.get('id', ''))
            v   = form_data.get(fid, '')
            if ft in _ALWAYS_SHOW_PRE:
                return True
            if ft == 'rating'        and str(v).isdigit() and int(v) > 0:  return True
            if ft == 'checkbox'      and v in ('yes','true'):               return True
            if ft == 'checkbox_group'and isinstance(v, list) and v:        return True
            if ft == 'image'         and v and os.path.exists(os.path.join(static_folder, v)): return True
            if ft == 'signature'     and v and str(v).startswith('data:'): return True
            if ft == 'table'         and isinstance(v, list) and v:        return True
            if ft in ('number','date','email','radio','select') and v:      return True
            if ft in ('text','textarea')                         and v:      return True
        return False

    # Build a set of section-trigger row numbers that have visible content somewhere
    # in their subordinate rows (from their row_num up to the next section's row_num).
    section_row_nums = sorted(sections.keys())
    all_row_nums     = list(rows_map.keys())

    def _section_has_visible_rows(sec_row_num):
        idx = section_row_nums.index(sec_row_num)
        next_sec = section_row_nums[idx + 1] if idx + 1 < len(section_row_nums) else None
        for rn in all_row_nums:
            if rn < sec_row_num:
                continue
            if next_sec is not None and rn >= next_sec:
                break
            if _row_has_content(rows_map[rn]):
                return True
        return False

    # ── Render row by row ─────────────────────────────────────────────────────
    for row_num, fields_in_row in rows_map.items():

        # Emit section banner only if its subordinate rows have visible content
        if row_num in sections:
            if not _section_has_visible_rows(row_num):
                continue   # skip the banner — all rows beneath it are empty
            story.append(Spacer(1, 6))
            sec_label = sections[row_num]
            story.append(Paragraph(
                sec_label,
                ParagraphStyle('SecBanner', fontName='Helvetica-Bold',
                               fontSize=10, textColor=C_DARK,
                               spaceBefore=8, spaceAfter=3),
            ))
            story.append(HRFlowable(width='100%', thickness=0.75,
                                     color=C_BORDER, spaceAfter=4))

        # Build one Table row with cells sized by colSpan.
        # Fields with no value are skipped unless they are text/textarea/label —
        # those always appear so free-text notes are never suppressed.
        cells      = []
        col_widths = []

        for field in fields_in_row:
            ftype    = field.get('type', '')
            col_span = field.get('colSpan', 1)
            cell_w   = UNIT * col_span

            # ── Inline label (free-standing text element) ─────────────────────
            if ftype == 'label':
                fs_map = {'small': 8, 'normal': 9, 'large': 11, 'x-large': 13}
                fs = fs_map.get(field.get('font_size', 'normal'), 9)
                fw = 'Helvetica-Bold' if field.get('font_weight') == 'bold' else 'Helvetica'
                p = Paragraph(
                    field.get('text_content', ''),
                    ParagraphStyle('li', fontName=fw, fontSize=fs,
                                   textColor=C_DARK, leading=fs + 3),
                )
                cells.append(p)
                col_widths.append(cell_w)
                continue

            fid = str(field.get('id', ''))
            val = form_data.get(fid, '')
            lbl = field.get('label', '')

            # ── Skip fields with no meaningful value ──────────────────────────
            # Returns True if the field should be hidden from the PDF.
            def _skip(ftype, val):
                if ftype == 'rating':
                    return not str(val).isdigit() or int(val) == 0
                if ftype == 'image':
                    img_path = os.path.join(static_folder, val) if val else ''
                    return not val or not os.path.exists(img_path)
                if ftype == 'signature':
                    return not val or not str(val).startswith('data:')
                if ftype == 'checkbox':
                    # stored as 'true'/'false' by _collect_form_responses
                    return val not in ('yes', 'true')
                if ftype == 'checkbox_group':
                    return not val or not isinstance(val, list) or len(val) == 0
                if ftype == 'table':
                    return not val or not isinstance(val, list) or len(val) == 0
                # All remaining input types (text, textarea, number, date,
                # email, radio, select, and any future types)
                return not val

            if _skip(ftype, val):
                continue

            lbl_p = Paragraph(lbl, STYLES['FieldLabel'])

            # ── Value content ────────────────────────────────────────────────
            if ftype == 'rating':
                score_int = int(val)
                # Use ★ for both filled and unfilled — ☆ is unsupported in
                # Helvetica and renders as a solid box. Grey colour for unfilled.
                filled_stars = '<font color="#f59e0b">' + ('★' * score_int) + '</font>'
                empty_stars  = ('<font color="#d1d5db">' + ('★' * (5 - score_int)) + '</font>'
                               if score_int < 5 else '')
                stars = filled_stars + empty_stars + f'  <font color="#64748b">{score_int}/5</font>'
                val_p = Paragraph(stars, ParagraphStyle(
                    'rv', fontName='Helvetica', fontSize=9, leading=12))

            elif ftype == 'image':
                # File existence already verified in the skip gate above
                img_path = os.path.join(static_folder, val)
                try:
                    val_p = RLImage(img_path,
                                    width=min(cell_w - 12, 1.4 * inch),
                                    height=1.0 * inch,
                                    kind='proportional')
                except Exception:
                    # Could not load image — skip this cell entirely
                    continue

            elif ftype == 'signature':
                val_p = Paragraph('[Signature captured]', STYLES['FieldValue'])

            elif ftype == 'checkbox':
                val_p = Paragraph('Yes', STYLES['FieldValue'])

            elif ftype == 'checkbox_group':
                val_p = Paragraph(',  '.join(val), STYLES['FieldValue'])

            elif ftype == 'table':
                headers  = field.get('col_headers',
                                     list(val[0].keys()) if val else [])
                tbl_data = ([headers] +
                            [[r.get(h, '') for h in headers] for r in val])
                val_p = Table(tbl_data)
                val_p.setStyle(TableStyle([
                    ('BACKGROUND',    (0, 0), (-1, 0),  C_LIGHT),
                    ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
                    ('FONTSIZE',      (0, 0), (-1, -1), 7),
                    ('INNERGRID',     (0, 0), (-1, -1), 0.25, C_BORDER),
                    ('BOX',           (0, 0), (-1, -1), 0.5,  C_BORDER),
                    ('TOPPADDING',    (0, 0), (-1, -1), 2),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ]))

            else:
                # text, textarea, number, date, email, radio, select
                disp = str(val) if val else ''
                val_p = Paragraph(disp, STYLES['FieldValue'])

            # ── Wrap into label-over-value form box ──────────────────────────
            box = Table([[lbl_p], [val_p]], colWidths=[cell_w - 4])
            box.setStyle(TableStyle([
                ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING',  (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING',   (0, 0), (0, 0),   2),
                ('BOTTOMPADDING',(0, 0), (0, 0),   1),
                ('TOPPADDING',   (0, 1), (0, 1),   3),
                ('BOTTOMPADDING',(0, 1), (0, 1),   4),
                ('BOX',          (0, 1), (0, 1),   0.5, C_BORDER),
                ('BACKGROUND',   (0, 1), (0, 1),   colors.HexColor('#f8fafc')),
            ]))

            cells.append(box)
            col_widths.append(cell_w)

        # Skip the entire row if nothing made it through the filter
        if not cells:
            continue

        row_tbl = Table([cells], colWidths=col_widths, hAlign='LEFT')
        row_tbl.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING',   (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 2),
        ]))
        story.append(row_tbl)

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