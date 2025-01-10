from flask import flask

def create_app():
    app =  Flask(__name__)

    app.config['SQLALCHEMY_TRACKER_MODIFICATIONS'] = false

    from.routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    return app
