from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, TextAreaField, DecimalField, BooleanField, IntegerField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Optional, NumberRange, ValidationError
from app.models.user import User

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=100)])
    password = PasswordField('Password', validators=[DataRequired()])

class UserForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=100)])
    email = StringField('Email', validators=[DataRequired(), Email(), Length(max=255)])
    password = PasswordField('Password', validators=[Length(min=6, max=100)])
    confirm_password = PasswordField('Confirm Password', validators=[EqualTo('password')])
    role = SelectField('Role', choices=[
        ('admin', 'Administrator'),
        ('supervisor', 'Supervisor'),
        ('inspector', 'Inspector')
    ], validators=[DataRequired()])
    
    def __init__(self, user=None, *args, **kwargs):
        super(UserForm, self).__init__(*args, **kwargs)
        self.user = user
    
    def validate_username(self, field):
        if self.user:
            # Editing existing user
            if field.data != self.user.username:
                if User.query.filter_by(username=field.data).first():
                    raise ValidationError('Username already exists.')
        else:
            # Creating new user
            if User.query.filter_by(username=field.data).first():
                raise ValidationError('Username already exists.')
    
    def validate_email(self, field):
        if self.user:
            if field.data != self.user.email:
                if User.query.filter_by(email=field.data).first():
                    raise ValidationError('Email already registered.')
        else:
            if User.query.filter_by(email=field.data).first():
                raise ValidationError('Email already registered.')

class FacilityForm(FlaskForm):
    name = StringField('Facility Name', validators=[DataRequired(), Length(max=255)])
    address = TextAreaField('Address', validators=[Optional()])
    contact_person = StringField('Contact Person', validators=[Optional(), Length(max=100)])
    contact_phone = StringField('Contact Phone', validators=[Optional(), Length(max=20)])
    active = BooleanField('Active', default=True)

class AreaForm(FlaskForm):
    name = StringField('Area Name', validators=[DataRequired(), Length(max=255)])
    area_type = SelectField('Area Type', choices=[
        ('restroom', 'Restroom'),
        ('lobby', 'Lobby'),
        ('hallway', 'Hallway'),
        ('office', 'Office'),
        ('kitchen', 'Kitchen'),
        ('storage', 'Storage'),
        ('outdoor', 'Outdoor'),
        ('other', 'Other')
    ], validators=[Optional()])
    facility_id = SelectField('Facility', coerce=int, validators=[DataRequired()])

class InspectionTemplateForm(FlaskForm):
    name = StringField('Template Name', validators=[DataRequired(), Length(max=255)])
    description = TextAreaField('Description', validators=[Optional()])
    frequency = SelectField('Inspection Frequency', choices=[
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('quarterly', 'Quarterly')
    ], validators=[DataRequired()])

class ChecklistItemForm(FlaskForm):
    category = StringField('Category', validators=[DataRequired(), Length(max=100)])
    item_description = TextAreaField('Item Description', validators=[DataRequired()])
    scoring_type = SelectField('Scoring Type', choices=[
        ('pass_fail', 'Pass/Fail'),
        ('rating_5', '5-Point Rating'),
        ('rating_10', '10-Point Rating')
    ], validators=[DataRequired()])
    weight = DecimalField('Weight', validators=[Optional(), NumberRange(min=0.1, max=10.0)], default=1.00)
    requires_photo = BooleanField('Requires Photo Evidence', default=False)
    display_order = IntegerField('Display Order', validators=[Optional()], default=0)