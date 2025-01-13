from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, SubmitField, SelectField
from wtforms.validators import DataRequired, Email, Length

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Register')

class BetForm(FlaskForm):
    match_id = StringField('Match ID', validators=[DataRequired()])
    bet_amount = FloatField('Bet Amount', validators=[DataRequired()])
    outcome = SelectField('Outcome', choices=[('win', 'Win'), ('lose', 'Lose')], validators=[DataRequired()])
    submit = SubmitField('Submit Bet')
