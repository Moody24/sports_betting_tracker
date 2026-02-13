# Sports Betting Tracker

A web application built with **Flask**, designed to help users manage and track their sports bets, including monitoring outcomes and calculating profits and losses. This project also allows users to log in, register, place bets, and view statistics on their betting history.

---

## Features

- **User Authentication**:
  - User registration and login functionality using **Flask-Login**.
  - Password hashing for security with **Werkzeug security utilities**.

- **Bet Management**:
  - Users can place, edit, and delete bets.
  - Each bet is associated with a specific match, amount, and outcome (win/lose).

- **User Dashboard**:
  - View a list of all placed bets.
  - See total amount wagered and other relevant statistics.

- **Database**:
  - Data stored using **SQLite** through **SQLAlchemy**.
  - Models for users, bets, and matches.

- **Environment Configuration**:
  - Sensitive information is stored in the `.env` file, which is properly ignored by Git using `.gitignore`.

---

## Tech Stack

- **Backend**:
  - **Flask**: Python web framework.
  - **SQLAlchemy**: ORM for database management.
  - **Flask-Login**: User session management.
  - **Werkzeug security**: Password hashing and verification.
  - **Flask-Migrate**: Database migrations.
  
- **Frontend**:
  - **Jinja2**: Template rendering.
  - **Bootstrap (Optional)**: For modern, responsive design (you may want to add it for better UI).

- **Database**:
  - **SQLite**: Lightweight database for storing user data, bets, and match details.

---

## Setup Instructions

1. **Clone the repository**:
   - In your terminal, run:
     ```bash
     git clone https://github.com/Moody24/sports_betting_tracker.git
     cd sports_betting_tracker
     ```

2. **Create and activate a virtual environment**:
   - **Linux/macOS**:
     ```bash
     python3 -m venv venv
     source venv/bin/activate
     ```
   - **Windows**:
     ```bash
     python -m venv venv
     venv\Scripts\activate
     ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   - Create a `.env` file in the root of the project with the following content:
     ```plaintext
     SECRET_KEY=your-secure-key
     DATABASE_URL=sqlite:///app.db
     FLASK_ENV=development
     FLASK_DEBUG=1
     ```

5. **Run the application**:
   ```bash
   python run.py
   ```

6. **Database Initialization** (if necessary):
   - Run the following to initialize the database (if you’re using migrations):
     ```bash
     flask db init
     flask db migrate
     flask db upgrade
     ```

---

## What Has Been Done So Far

- **Project Setup**:
  - Created Flask project with user authentication, betting management, and SQLite database.
  
- **Features Implemented**:
  - **User Registration & Login**: Users can register, log in, and log out.
  - **Bet Management**: Users can place, edit, and delete bets.
  - **Database Models**: Defined models for users, bets, and matches.
  
- **Security**:
  - Implemented password hashing with **Werkzeug security** to securely store user passwords.
  
- **Environment Configuration**:
  - Sensitive data like the secret key and database URL are stored in the `.env` file, which is ignored by Git using `.gitignore`.

- **Flask Extensions**:
  - Used **Flask-Login** for user session management and **Flask-Migrate** for database migrations.

---

## Next Steps / TODO

- **UI Improvement**:
  - Add a modern front-end framework (e.g., **Bootstrap** or **Tailwind CSS**) to improve the user interface.
  
- **Real-Time Data Integration**:
  - Integrate sports data or betting odds via an external API (e.g., [SportsRadar](https://developers.sportradar.com/), [Football-Data.org](https://www.football-data.org/)).

- **Add Testing**:
  - Introduce unit and integration tests using **pytest** to ensure application reliability.

- **Deployment**:
  - Deploy the app to **Heroku**, **AWS**, or **Render** for public access.

---

## Contributing

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature-branch`).
3. Commit your changes (`git commit -am 'Add new feature'`).
4. Push to the branch (`git push origin feature-branch`).
5. Create a pull request.

---

## Codespaces: Sync & Verification Quick Guide

If you are working in GitHub Codespaces and want to confirm you actually pulled the latest work, use this flow:

1. **Update local refs and check your branch**
   ```bash
   git fetch --all --prune
   git branch --show-current
   git status
   ```

2. **Inspect recent commits**
   ```bash
   git log --oneline -n 5
   ```

3. **If you need a specific commit, verify it exists first**
   ```bash
   git show --oneline --no-patch <commit-hash>
   ```
   If Git returns `fatal: bad revision`, that commit is not present in your local clone or any fetched remote refs.

4. **Do not type angle brackets literally**
   - `git branch --contains <new_commit_hash>` will fail if copied exactly.
   - Replace `<new_commit_hash>` with a real hash, e.g.:
     ```bash
     git branch --contains 689d5ba
     ```

5. **If a feature branch already exists locally**
   ```bash
   git checkout feature/dashboard-on-main
   git pull --ff-only origin feature/dashboard-on-main
   ```
   Use `git checkout -b ...` only when creating a brand-new branch.

6. **If `main` and the feature branch show the same latest commit**
   - Example: both `main` and `feature/dashboard-on-main` point to the same hash in `git log` output.
   - Meaning: there are no new commits on the remote branch to pull yet.
   - Verify with:
     ```bash
     git log --oneline --decorate -n 5
     git branch -a
     ```
   - Next actions:
     1. Push the missing work from the environment where it was created, **or**
     2. Re-apply changes in your current branch, commit, and push:
        ```bash
        git checkout -b feature/reapply-dashboard
        # make edits
        git add .
        git commit -m "Reapply dashboard and registration improvements"
        git push -u origin feature/reapply-dashboard
        ```

7. **If `flask db` fails with `ModuleNotFoundError: No module named 'flask_bcrypt'`**
   This error means your checkout still has older code that imports `flask_bcrypt` in `app/__init__.py`.

   Fix it with this sequence in Codespaces:
   ```bash
   git checkout main
   git fetch --all --prune
   git pull origin main
   pip install -r requirements.txt
   ```

   Then verify your `app/__init__.py` no longer imports `flask_bcrypt`:
   ```bash
   rg "flask_bcrypt|Bcrypt" app/__init__.py
   ```

   Re-run migrations:
   ```bash
   flask --app run.py db upgrade
   ```

   Why this happens:
   - `flask db` loads your Flask app first.
   - If app import fails, Flask cannot register the migrate command, so you also see `Error: No such command 'db'`.

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
