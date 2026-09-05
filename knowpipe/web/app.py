"""Flask 应用工厂：注册 Blueprint、配置 session 密钥、初始化 MongoDB 连接。"""
from __future__ import annotations

import os

from flask import Flask

from . import mongo_sink
from .routes_api import api_bp
from .routes_pages import pages_bp


def create_app(db=None, secret_key: str | None = None) -> Flask:
    """构造 Flask 应用。db 缺省时按环境变量 MONGO_URI/MONGO_DB 连接真实 MongoDB
    （供 `python3 -m knowpipe.web.app` 启动使用）；测试中直接传入 mongomock 的 db。"""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = secret_key or os.environ.get("SECRET_KEY", "dev-secret-key")

    if db is None:
        from pymongo import MongoClient
        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        mongo_db_name = os.environ.get("MONGO_DB")
        client = MongoClient(mongo_uri)
        db = client[mongo_db_name] if mongo_db_name else client.get_default_database()

    mongo_sink.ensure_indexes(db)
    app.config["MONGO_DB"] = db

    app.register_blueprint(api_bp)
    app.register_blueprint(pages_bp)
    return app


if __name__ == "__main__":
    create_app().run(debug=True)
