"""登录鉴权：密码哈希、session 登录态管理、越权校验（research.md §1）。"""
from __future__ import annotations

import logging
from functools import wraps
from typing import Any, Callable

from flask import jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import mongo_sink

logger = logging.getLogger(__name__)


def hash_password(raw_password: str) -> str:
    return generate_password_hash(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, raw_password)


def login_user(user_id: str) -> None:
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


def logout_user() -> None:
    session.clear()


def current_user_id() -> str | None:
    return session.get("user_id")


def login_required(db_getter: Callable[[], Any]) -> Callable:
    """返回一个装饰器：校验 session 中的 user_id 存在且对应用户在 users 集合中确实存在。

    db_getter 延迟获取 MongoDB db 句柄（避免在装饰阶段就绑定到某个 db 实例）。
    """
    def decorator(view_func: Callable) -> Callable:
        @wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any):
            user_id = current_user_id()
            if not user_id:
                logger.info("拒绝未登录请求：%s", view_func.__name__)
                return jsonify({"error": "unauthorized"}), 401
            if mongo_sink.find_user_by_id(db_getter(), user_id) is None:
                logger.warning("session 中的 user_id=%s 在 users 集合中不存在，拒绝：%s",
                                user_id, view_func.__name__)
                return jsonify({"error": "unauthorized"}), 401
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


def check_owns_resource(requested_user_id: str) -> bool:
    """比较请求参数中的 user_id 与 session 中的 user_id 是否一致（对应 FR-005）。"""
    return current_user_id() == requested_user_id


def forbidden_response():
    logger.warning("拒绝越权请求：session user_id=%s 请求了其他 user_id", current_user_id())
    return jsonify({"error": "forbidden"}), 403
