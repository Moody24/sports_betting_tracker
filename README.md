# Sports Betting Tracker

Sports Betting Tracker is a Flask web application for recording bets, tracking outcomes, and understanding betting performance over time.

## Features

- User registration, login, and logout with secure password hashing.
- Create, edit, and delete bets.
- Dashboard with betting history and key totals.
- Relational data model for users, bets, and matches.
- Database migrations with Flask-Migrate.
- Model retraining artifacts can be stored in AWS S3.
- Environment-based configuration via `.env`.

## Tech Stack

- **Backend:** Flask, SQLAlchemy, Flask-Login, Flask-Migrate, Gunicorn
- **Frontend:** Jinja2 templates (Bootstrap-friendly)
- **Database:** Neon Postgres (via `DATABASE_URL`)
- **Storage:** AWS S3 (for retrained model artifacts)
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
   Then update values as needed (minimum expected keys include `SECRET_KEY` and `DATABASE_URL`; add S3 vars if you store retrained model artifacts in AWS).
   Example Neon connection string:
   ```env
   DATABASE_URL=postgresql://<user>:<password>@<your-neon-host>/<database>?sslmode=require
   ```

   > If your provider gives a URL that starts with `postgres://`, the app automatically rewrites it to `postgresql://` for SQLAlchemy compatibility.

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

## Database Notes (Neon Postgres)

- This project is intended to run against Neon Postgres in development and production.
- Ensure your Neon database is reachable from your runtime environment and that SSL is enabled (`sslmode=require`).
- Run migrations whenever the schema changes:
  ```bash
  flask --app run.py db upgrade heads
  ```


## Model Artifact Storage (AWS S3)

Retrained model artifacts can be persisted to an AWS S3 bucket.

Set these environment variables when using S3-backed model storage:

```env
MODEL_STORAGE=s3
S3_MODEL_BUCKET=your-s3-bucket-name
S3_MODEL_PREFIX=models/
AWS_REGION=us-east-1
```

If `MODEL_STORAGE` is not `s3`, the app falls back to local filesystem paths for model artifacts.

## Deployment Notes

- Railway deploys should run only after CI tests pass.
- Railway is enough to deploy and run this app.
- Vercel is optional and can be used as a proxy/edge layer in front of Railway.
- Detailed Railway + Neon runbook: `docs/deploy.md`.
- UI visual QA baseline checklist: `docs/ui_v1_baseline.md`.

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
