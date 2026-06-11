"""用户认证 (`users` 表) —— Peewee CRUD + PBKDF2-HMAC-SHA256 哈希。

密码用 stdlib `hashlib.pbkdf2_hmac(SHA-256)`,无外部依赖。
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime

from peewee import IntegrityError

from .models import User

_ITERATIONS = 600_000  # PBKDF2-SHA256 的 OWASP 推荐迭代数


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    """返回 `(hex_hash, hex_salt)`。"""
    if salt is None:
        salt = os.urandom(32)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return h.hex(), salt.hex()


def register(username: str, password: str) -> bool:
    """新建用户。成功返回 True;用户名已存在(UNIQUE 冲突)返回 False。"""
    pw_hash, pw_salt = _hash_password(password)
    try:
        User.create(
            username=username.lower(),
            pw_hash=pw_hash, pw_salt=pw_salt,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        return True
    except IntegrityError:
        return False


def get_user(username: str) -> User | None:
    """按用户名取一条 -> `User`(含 hash/salt)或 None。"""
    return User.get_or_none(User.username == username.lower())


def verify(username: str, password: str) -> bool:
    """校验用户名+密码,返回是否匹配。"""
    user = get_user(username)
    if user is None:
        return False
    candidate_hash, _ = _hash_password(password, bytes.fromhex(user.pw_salt))
    return candidate_hash == user.pw_hash


def change_password(username: str, new_password: str) -> bool:
    """重置密码。返回用户是否存在。"""
    pw_hash, pw_salt = _hash_password(new_password)
    n = (User
         .update(pw_hash=pw_hash, pw_salt=pw_salt)
         .where(User.username == username.lower())
         .execute())
    return n > 0


def delete_user(username: str) -> bool:
    """删除用户。返回用户是否存在(并被删)。"""
    return User.delete().where(User.username == username.lower()).execute() > 0


def list_users() -> list[User]:
    """列出全部用户 -> `User` 列表(含 hash/salt;CLI 仅展示 username/created_at)。"""
    return list(User.select().order_by(User.created_at))


def has_users() -> bool:
    """是否至少注册了一个用户(决定是否启用登录门禁)。"""
    return User.select().count() > 0
