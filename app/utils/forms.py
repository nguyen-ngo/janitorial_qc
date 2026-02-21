from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import (StringField, PasswordField, SelectField, TextAreaField,
                     DecimalField, BooleanField, IntegerField, HiddenField,
                     RadioField)
from wtforms.validators import (DataRequired, Email, Length, EqualTo,
                                Optional, NumberRange, ValidationError)
from app.models.user import User


# ── Auth ─────────────────────────────────────────────────────────────────────

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=100)])
    password = PasswordField('Password', validators=[DataRequired()])


class UserForm(FlaskForm):
    username        = StringField('Username', validators=[DataRequired(), Length(min=3, max=100)])
    email           = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    password        = PasswordField('Password', validators=[Length(min=6, max=100)])
    confirm_password = PasswordField('Confirm Password', validators=[EqualTo('password')])
    role            = SelectField('Role', choices=[
        ('admin', 'Administrator'), ('supervisor', 'Supervisor'), ('inspector', 'Inspector')
    ], validators=[DataRequired()])

    def __init__(self, user=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def validate_username(self, field):
        q = User.query.filter_by(username=field.data).first()
        if self.user:
            if field.data != self.user.username and q:
                raise ValidationError('Username already exists.')
        elif q:
            raise ValidationError('Username already exists.')

    def validate_email(self, field):
        q = User.query.filter_by(email=field.data).first()
        if self.user:
            if field.data != self.user.email and q:
                raise ValidationError('Email already registered.')
        elif q:
            raise ValidationError('Email already registered.')


# ── Facility / Area ──────────────────────────────────────────────────────────

class FacilityForm(FlaskForm):
    name           = StringField('Facility Name', validators=[DataRequired(), Length(max=255)])
    address        = TextAreaField('Address', validators=[Optional()])
    contact_person = StringField('Contact Person', validators=[Optional(), Length(max=100)])
    contact_phone  = StringField('Contact Phone',  validators=[Optional(), Length(max=20)])
    active         = BooleanField('Active', default=True)


class AreaForm(FlaskForm):
    name      = StringField('Area Name', validators=[DataRequired(), Length(max=255)])
    area_type = SelectField('Area Type', choices=[
        ('restroom','Restroom'), ('lobby','Lobby'), ('hallway','Hallway'),
        ('office','Office'), ('kitchen','Kitchen'), ('storage','Storage'),
        ('outdoor','Outdoor'), ('other','Other'),
    ], validators=[Optional()])
    facility_id = SelectField('Facility', coerce=int, validators=[DataRequired()])


# ── Templates ────────────────────────────────────────────────────────────────

class InspectionTemplateForm(FlaskForm):
    name        = StringField('Template Name', validators=[DataRequired(), Length(max=255)])
    description = TextAreaField('Description', validators=[Optional()])
    frequency   = SelectField('Inspection Frequency', choices=[
        ('daily','Daily'), ('weekly','Weekly'),
        ('monthly','Monthly'), ('quarterly','Quarterly'),
    ], validators=[DataRequired()])


class ChecklistItemForm(FlaskForm):
    category         = StringField('Category', validators=[DataRequired(), Length(max=100)])
    item_description = TextAreaField('Item Description', validators=[DataRequired()])
    scoring_type     = SelectField('Scoring Type', choices=[
        ('pass_fail','Pass/Fail'), ('rating_5','5-Point Rating'), ('rating_10','10-Point Rating'),
    ], validators=[DataRequired()])
    weight         = DecimalField('Weight', validators=[Optional(), NumberRange(min=0.1, max=10.0)], default=1.00)
    requires_photo = BooleanField('Requires Photo Evidence', default=False)
    display_order  = IntegerField('Display Order', validators=[Optional()], default=0)


# ── Inspections ──────────────────────────────────────────────────────────────

class StartInspectionForm(FlaskForm):
    template_id  = SelectField('Template', coerce=int, validators=[DataRequired()])
    facility_id  = SelectField('Facility', coerce=int, validators=[DataRequired()])
    area_id      = SelectField('Area (optional)', coerce=int, validators=[Optional()])
    notes        = TextAreaField('Notes', validators=[Optional()])


class ChecklistResultForm(FlaskForm):
    """Dynamically rendered per checklist item — base validators only."""
    score    = DecimalField('Score', validators=[Optional(), NumberRange(min=0, max=10)])
    passed   = HiddenField('Passed')          # 'true' / 'false' / ''
    comments = TextAreaField('Comments', validators=[Optional(), Length(max=1000)])
    photo    = FileField('Photo', validators=[
        Optional(),
        FileAllowed(['jpg','jpeg','png','gif'], 'Images only.')
    ])


# ── Issues ───────────────────────────────────────────────────────────────────

class IssueForm(FlaskForm):
    area_id     = SelectField('Area', coerce=int, validators=[DataRequired()])
    severity    = SelectField('Severity', choices=[
        ('low','Low'), ('medium','Medium'), ('high','High'), ('critical','Critical'),
    ], validators=[DataRequired()])
    description = TextAreaField('Description', validators=[DataRequired(), Length(max=2000)])
    photo       = FileField('Photo Evidence', validators=[
        Optional(),
        FileAllowed(['jpg','jpeg','png','gif'], 'Images only.')
    ])
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])


class IssueUpdateForm(FlaskForm):
    status      = SelectField('Status', choices=[
        ('open','Open'), ('in_progress','In Progress'), ('resolved','Resolved'),
    ], validators=[DataRequired()])
    assigned_to = SelectField('Assign To', coerce=int, validators=[Optional()])
    comments    = TextAreaField('Update Notes', validators=[Optional(), Length(max=1000)])
