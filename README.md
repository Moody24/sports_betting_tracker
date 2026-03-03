# Sports Betting Tracker

Sports Betting Tracker is a Flask web application for recording bets, tracking outcomes, and understanding betting performance over time.

## Features

- User registration, login, and logout with secure password hashing.
- Create, edit, and delete bets.
- Dashboard with betting history and key totals.
- Relational data model for users, bets, and matches.
- Database migrations with Flask-Migrate.
- Environment-based configuration via `.env`.

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-Migrate, Gunicorn
- **Frontend:** Jinja2 templates (Bootstrap-friendly)
- **Database:** SQLite by default (configurable through `DATABASE_URL`)
- **Deployment:** Docker / Railway

## Quick Start (Local)

1. **Clone the repository**
   ```bash
   git clone https://github.com/Moody24/sports_betting_tracker.git
   cd sports_betting_tracker
   ```

2. **Create and activate a virtual environment**
   - Linux/macOS:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```
   - Windows:
     ```bash
     python -m venv venv
     venv\Scripts\activate
     ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create `.env` from the example**
   ```bash
   cp .env.example .env
   ```
   Then update values as needed (minimum expected keys include `SECRET_KEY` and `DATABASE_URL`).

5. **Run database migrations**
   ```bash
   flask --app run.py db upgrade heads
   ```

6. **Start the app**
   ```bash
   python run.py
   ```

> By default, startup does **not** auto-run migrations. Set `AUTO_DB_UPGRADE=true` only if you explicitly want boot-time migrations.

## Run with Docker

```bash
docker compose up --build
```

The container entrypoint runs:

```bash
SCHEDULER_ENABLED=false flask --app run.py db upgrade heads
```

before starting Gunicorn.

## Deployment Notes

- Railway is enough to deploy and run this app.
- Vercel is optional and can be used as a proxy/edge layer in front of Railway.

## Common Git/Codespaces Checks

If you need to verify your branch is up to date:

```bash
git fetch --all --prune
git branch --show-current
git status
git log --oneline -n 5
```

Useful reminder: replace placeholders like `<commit-hash>` with an actual hash when running git commands.

## Contributing

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/your-change`).
3. Commit your changes.
4. Push your branch.
5. Open a pull request.
