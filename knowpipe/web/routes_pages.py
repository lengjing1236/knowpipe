"""登录/注册/单页 Web 的页面路由（Jinja2 服务端渲染 + 原生 JS 调用 /api/*）。"""
from __future__ import annotations

from flask import Blueprint, render_template

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/login")
def login_page():
    return render_template("login.html")


@pages_bp.get("/")
def index_page():
    return render_template("index.html")


@pages_bp.get("/learning")
def learning_page():
    return render_template("learning.html")
