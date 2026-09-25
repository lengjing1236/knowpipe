"""Application factory shared by local development and production WSGI."""
from __future__ import annotations

import os
from datetime import timedelta
from urllib.parse import urlsplit

from flask import Flask, jsonify
from pymongo.errors import PyMongoError
from werkzeug.middleware.proxy_fix import ProxyFix

from . import mongo_sink
from .routes_api import api_bp
from .routes_pages import pages_bp
from .routes_podcasts import podcast_bp
from .routes_learning import learning_bp
from ..learning.store import ensure_indexes as ensure_learning_indexes
from ..recommendations.queue import ensure_indexes as ensure_recommendation_indexes
from ..podcasts.store import ensure_indexes as ensure_podcast_indexes
from .security import install_security


def create_app(db=None, secret_key: str | None = None, config: dict | None = None) -> Flask:
    app = Flask(__name__)
    production = os.environ.get('APP_ENV', 'development') == 'production'
    key = secret_key or os.environ.get('SECRET_KEY', 'dev-secret-key')
    origin = os.environ.get('PUBLIC_ORIGIN', '')
    if production:
        parsed = urlsplit(origin)
        if len(key) < 32 or key == 'dev-secret-key' or key.startswith('replace-with-'):
            raise ValueError('Production requires a random SECRET_KEY of at least 32 characters')
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/') or parsed.username or parsed.query or parsed.fragment:
            raise ValueError('Production requires PUBLIC_ORIGIN=https://your-domain')
    app.config.update(
        SECRET_KEY=key, PRODUCTION=production, DEBUG=False,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=production, PERMANENT_SESSION_LIFETIME=timedelta(days=7),
        MAX_CONTENT_LENGTH=3 * 1024 * 1024, CSRF_ENABLED=True,
        AUTH_RATE_LIMIT_ENABLED=production, AUTH_RATE_LIMIT=20,
    )
    if config:
        app.config.update(config)
    if production:
        app.config.update(CSRF_ENABLED=True, SESSION_COOKIE_SECURE=True, DEBUG=False)
        app.config['TRUSTED_HOSTS'] = [urlsplit(origin).hostname]
    if os.environ.get('TRUST_PROXY') == '1':
        # Valid only when the WSGI port is private behind exactly one trusted proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if db is None:
        from pymongo import MongoClient
        client = MongoClient(os.environ.get('MONGO_URI', 'mongodb://localhost:27017'),
                             serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
        db_name = os.environ.get('MONGO_DB')
        db = client[db_name] if db_name else client.get_default_database('knowpipe_mining')
    mongo_sink.ensure_indexes(db)
    ensure_podcast_indexes(db)
    ensure_learning_indexes(db)
    ensure_recommendation_indexes(db)
    app.config['MONGO_DB'] = db
    install_security(app)
    app.register_blueprint(api_bp)
    app.register_blueprint(pages_bp)
    app.register_blueprint(podcast_bp)
    app.register_blueprint(learning_bp)

    @app.get('/health/live')
    def live():
        return jsonify(status='ok')

    @app.get('/health/ready')
    def ready():
        try:
            db.command('ping')
        except PyMongoError:
            return jsonify(status='unavailable'), 503
        return jsonify(status='ready')

    return app


if __name__ == '__main__':
    create_app().run(host=os.environ.get('HOST', '127.0.0.1'),
                     port=int(os.environ.get('PORT', '5000')), debug=False)
