# Sports Betting Tracker

A web application built with **Flask**, designed to help users manage and track their sports bets, including monitoring outcomes and calculating profits and losses. This project also allows users to log in, register, place bets, and view statistics on their betting history.

---

## Features

- **User Authentication**:
  - User registration and login functionality using **Flask-Login**.
  - Password hashing for security with **Flask-Bcrypt**.

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
  - **Flask-Bcrypt**: Password hashing.
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
  - Implemented password hashing with **Flask-Bcrypt** to securely store user passwords.
  
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

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---



