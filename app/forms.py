from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, FloatField
from wtforms.validators import DataRequired, Email, Length

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign Up')

class BetForm(FlaskForm):
    match_id = StringField('Match ID', validators=[DataRequired()])
    bet_amount = FloatField('Bet Amount', validators=[DataRequired()])
    submit = SubmitField('Place Bet')
