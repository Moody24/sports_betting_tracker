from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    DateField,
    FloatField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, EqualTo, Length, NumberRange


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Length(max=120)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField(
        'Confirm Password',
        validators=[DataRequired(), EqualTo('password', message='Passwords must match.')],
    )
    submit = SubmitField('Register')


class BetForm(FlaskForm):
    team_a = StringField('Team A', validators=[DataRequired(), Length(max=80)])
    team_b = StringField('Team B', validators=[DataRequired(), Length(max=80)])
    match_date = DateField('Match Date', validators=[DataRequired()], format='%Y-%m-%d')
    bet_amount = FloatField(
        'Bet Amount',
        validators=[DataRequired(), NumberRange(min=1, message='Bet amount must be greater than zero.')],
    )
    outcome = SelectField(
        'Outcome',
        choices=[('pending', 'Pending'), ('win', 'Win'), ('lose', 'Lose')],
        default='pending',
        validators=[DataRequired()],
    )
    submit = SubmitField('Submit Bet')


class FanDuelImportForm(FlaskForm):
    csv_file = FileField(
        'FanDuel CSV Export',
        validators=[FileRequired(), FileAllowed(['csv'], 'CSV files only!')],
    )
    submit = SubmitField('Import Bets')


class DeleteBetForm(FlaskForm):
    submit = SubmitField('Delete')


class LogoutForm(FlaskForm):
    submit = SubmitField('Logout')
