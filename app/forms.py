from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, FloatField, SubmitField, BooleanField, DateField, SelectField, HiddenField, TextAreaField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)


COMMON_PASSWORDS = frozenset({
    '123456789012',
    'adminadmin',
    'letmeinletmein',
    'password123',
    'passwordpassword',
    'qwertyqwerty',
    'welcome12345',
})


def reject_common_password(_form, field) -> None:
    normalized = ''.join(str(field.data or '').casefold().split())
    if normalized in COMMON_PASSWORDS:
        raise ValidationError('That password is too common. Choose a unique passphrase.')


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    email = StringField('Email', validators=[DataRequired(), Email(message='Enter a valid email address.'), Length(max=120)])
    password = PasswordField(
        'Password',
        validators=[DataRequired(), Length(min=12, max=256), reject_common_password],
    )
    confirm_password = PasswordField(
        'Confirm Password',
        validators=[
            DataRequired(),
            Length(max=256),
            EqualTo('password', message='Passwords must match.'),
        ],
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
    bet_type = SelectField(
        'Bet Type',
        choices=[('moneyline', 'Moneyline'), ('over', 'Over'), ('under', 'Under')],
        default='moneyline',
        validators=[DataRequired()],
    )
    over_under_line = FloatField('O/U Line', validators=[Optional()])
    outcome = SelectField(
        'Outcome',
        choices=[('pending', 'Pending'), ('win', 'Win'), ('lose', 'Lose')],
        default='pending',
        validators=[DataRequired()],
    )
    picked_team = StringField('Picked Team (Moneyline)', validators=[Optional(), Length(max=80)])
    external_game_id = HiddenField('Game ID')
    notes = TextAreaField('Notes / Reasoning', validators=[Optional(), Length(max=1000)])
    submit = SubmitField('Submit Bet')


class DeleteBetForm(FlaskForm):
    submit = SubmitField('Delete')


class LogoutForm(FlaskForm):
    submit = SubmitField('Logout')
