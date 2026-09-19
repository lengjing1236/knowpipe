"""Same-origin session protection and a shared Mongo fixed-window auth limiter."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from flask import jsonify, request, session
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def install_security(app):
    db = app.config['MONGO_DB']
    db.auth_limits.create_index('expires_at', expireAfterSeconds=0)

    @app.before_request
    def protect_request():
        if request.path.startswith('/api/') and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            if app.config['CSRF_ENABLED']:
                expected = session.get('csrf_token', '')
                supplied = request.headers.get('X-CSRF-Token', '')
                if not expected or not hmac.compare_digest(expected, supplied):
                    return jsonify(error='csrf_failed'), 403
            if request.method != 'DELETE' and not isinstance(request.get_json(silent=True), dict):
                return jsonify(error='invalid_json'), 400
        if app.config['AUTH_RATE_LIMIT_ENABLED'] and request.path in {
            '/api/auth/login', '/api/auth/register'
        } and request.method == 'POST':
            window = int(time.time()) // 60
            identity = hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()
            key = f'{window}:{identity}'
            update = {'$inc': {'count': 1}, '$setOnInsert': {
                'expires_at': datetime.fromtimestamp((window + 2) * 60, timezone.utc)}}
            try:
                record = db.auth_limits.find_one_and_update(
                    {'_id': key}, update, upsert=True, return_document=ReturnDocument.AFTER)
            except DuplicateKeyError:
                record = db.auth_limits.find_one_and_update(
                    {'_id': key}, {'$inc': {'count': 1}}, return_document=ReturnDocument.AFTER)
            if record['count'] > app.config['AUTH_RATE_LIMIT']:
                return jsonify(error='rate_limited'), 429, {'Retry-After': str(60 - int(time.time()) % 60)}

    @app.after_request
    def response_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if app.config['PRODUCTION']:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='request_too_large'), 413

    @app.errorhandler(500)
    def server_error(_):
        return jsonify(error='internal_error'), 500
