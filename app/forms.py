from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, SubmitField, BooleanField, DateField, SelectField
from wtforms.validators import DataRequired, Email, Length, EqualTo, NumberRange

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired(),
        EqualTo('password', message='Passwords must match.')
    ])
    submit = SubmitField('Register')

class BetForm(FlaskForm):
    team_a = StringField('Team A', validators=[DataRequired(), Length(max=80)])
    team_b = StringField('Team B', validators=[DataRequired(), Length(max=80)])
    match_date = DateField('Match Date', validators=[DataRequired()], format='%Y-%m-%d')
    bet_amount = FloatField('Bet Amount', validators=[DataRequired(), NumberRange(min=1, message='Bet amount must be greater than zero.')])
    outcome = SelectField('Outcome', choices=[('pending', 'Pending'), ('win', 'Win'), ('lose', 'Lose')], default='pending', validators=[DataRequired()])
    submit = SubmitField('Submit Bet')


