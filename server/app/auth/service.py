"""认证核心逻辑：argon2id 哈希、会话签发/解析/吊销、登录失败审计。

安全约束（docs/12-permission-matrix.md §3、ADR-004）：
- 密码哈希只用 argon2id（argon2-cffi `PasswordHasher`），明文只存在于请求体
- 会话 token 用 `secrets.token_urlsafe(32)` 生成，数据库只存 sha256 十六进制
- CSRF token 与会话一一绑定（独立随机值），随 `GET /auth/me` 下发
- 账号不存在与密码错误返回同一个 401 `AUTH_INVALID_CREDENTIALS`，并做耗时对齐
- 账号停用（`is_active=false`）后解析会话一律失败 → 现有会话立即 401
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.audit import service as audit
from app.errors import AppError
from app.models.auth_session import AuthSession
from app.models.user import User
from app.settings import Settings

SESSION_COOKIE_NAME = "session_id"
CSRF_HEADER_NAME = "X-CSRF-Token"
SESSION_TOKEN_BYTES = 32
CSRF_TOKEN_BYTES = 32
DEFAULT_SESSION_TTL_HOURS = 8
INVALID_CREDENTIALS_MESSAGE = "用户名或密码错误"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_SECURE_ENV_NAMES = frozenset({"staging", "prod", "production"})

# argon2id（argon2-cffi 默认参数：time_cost=3, memory_cost=64MiB, parallelism=4）
_hasher = PasswordHasher()
# 账号不存在时也执行一次校验，避免响应耗时泄露账号是否存在
_DUMMY_PASSWORD_HASH = _hasher.hash("timing-equalisation-dummy-password")


# --------------------------------------------------------------------------- #
# 密码
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    """生成 argon2id 哈希（明文不落库）。"""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """校验密码；哈希损坏或不匹配都返回 False（不抛异常）。"""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """argon2 参数升级后判断是否需要重算哈希。"""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _waste_verification_time(password: str) -> None:
    with contextlib.suppress(VerifyMismatchError, VerificationError, InvalidHashError):
        _hasher.verify(_DUMMY_PASSWORD_HASH, password)


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
def hash_session_token(token: str) -> str:
    """会话 token 的存储形态：sha256 十六进制（64 字符）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_ttl(settings: Settings | None = None) -> timedelta:
    """会话有效期（settings.session_ttl_hours，默认 8h）。"""
    hours = settings.session_ttl_hours if settings is not None else DEFAULT_SESSION_TTL_HOURS
    return timedelta(hours=hours)


def cookie_secure(settings: Settings) -> bool:
    """Cookie 的 Secure 标志。

    settings.py 由 TASK-BE-000 持有（本卡不可修改），因此按以下顺序解析：
    1. settings 上若存在 `session_cookie_secure` 字段则直接用；
    2. `app_env=test` 固定关闭（测试走 http，避免外部环境变量干扰）；
    3. 环境变量 `SESSION_COOKIE_SECURE`（true/1/yes/on）；
    4. 默认：staging/prod 开启，其余关闭。
    """
    override = getattr(settings, "session_cookie_secure", None)
    if isinstance(override, bool):
        return override
    app_env = settings.app_env.strip().lower()
    if app_env == "test":
        return False
    raw = os.environ.get("SESSION_COOKIE_SECURE")
    if raw is not None:
        return raw.strip().lower() in _TRUE_VALUES
    return app_env in _SECURE_ENV_NAMES


def as_utc(value: datetime) -> datetime:
    """SQLite 读回的时间可能丢失时区，统一按 UTC 处理后再比较。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_session_valid(session: AuthSession, now: datetime | None = None) -> bool:
    """未吊销且未过期。"""
    if session.revoked_at is not None:
        return False
    return as_utc(session.expires_at) > (now or datetime.now(UTC))


def create_session(
    db: Session,
    user: User,
    settings: Settings | None = None,
    *,
    ttl_hours: int | None = None,
) -> tuple[str, AuthSession]:
    """创建会话；返回 (明文 token, 会话记录)。明文 token 只回给 Cookie。"""
    token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    ttl = timedelta(hours=ttl_hours) if ttl_hours is not None else session_ttl(settings)
    now = datetime.now(UTC)
    session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        csrf_token=secrets.token_urlsafe(CSRF_TOKEN_BYTES),
        expires_at=now + ttl,
        created_at=now,
    )
    db.add(session)
    db.flush()
    return token, session


def get_active_session(db: Session, token: str) -> tuple[AuthSession, User] | None:
    """按明文 token 解析出有效会话与用户；过期/吊销/停用一律返回 None。"""
    if not token:
        return None
    stmt = (
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_session_token(token))
    )
    row = db.execute(stmt).first()
    if row is None:
        return None
    session, user = row
    if not user.is_active or not is_session_valid(session):
        return None
    return session, user


def revoke_session(db: Session, session: AuthSession, now: datetime | None = None) -> None:
    """吊销单个会话（幂等）。"""
    if session.revoked_at is None:
        session.revoked_at = now or datetime.now(UTC)
        db.flush()


def revoke_all_sessions(db: Session, user_id: int, now: datetime | None = None) -> int:
    """吊销某账号全部未吊销会话（停用账号 / 重置密码时调用），返回处理条数。"""
    sessions = db.scalars(
        select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
        )
    ).all()
    revoked_at = now or datetime.now(UTC)
    revoked = 0
    for session in sessions:
        session.revoked_at = revoked_at
        revoked += 1
    if revoked:
        db.flush()
    return revoked


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #
def login(
    db: Session,
    *,
    username: str,
    password: str,
    settings: Settings | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[User, AuthSession, str]:
    """登录：失败统一 401 AUTH_INVALID_CREDENTIALS 并记审计；成功返回会话。"""
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        _waste_verification_time(password)
        audit.record(
            db,
            action=audit.AUTH_LOGIN_FAILED,
            ip=ip,
            user_agent=user_agent,
            detail={"username": username, "reason": "unknown_user"},
        )
        db.commit()
        raise AppError(errors.AUTH_INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE, 401)

    if not verify_password(user.password_hash, password):
        audit.record(
            db,
            action=audit.AUTH_LOGIN_FAILED,
            ip=ip,
            user_agent=user_agent,
            detail={"username": username, "reason": "bad_credentials"},
        )
        db.commit()
        raise AppError(errors.AUTH_INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE, 401)

    if not user.is_active:
        audit.record(
            db,
            action=audit.AUTH_LOGIN_FAILED,
            ip=ip,
            user_agent=user_agent,
            detail={"username": username, "reason": "inactive"},
        )
        db.commit()
        raise AppError(errors.AUTH_INVALID_CREDENTIALS, INVALID_CREDENTIALS_MESSAGE, 401)

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    token, session = create_session(db, user, settings)
    audit.record(
        db,
        action=audit.AUTH_LOGIN,
        actor_id=user.id,
        target_type="user",
        target_id=user.id,
        ip=ip,
        user_agent=user_agent,
        detail={"username": user.username},
    )
    db.commit()
    return user, session, token


def logout(
    db: Session,
    session: AuthSession,
    *,
    actor_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """登出：吊销当前会话并写审计。"""
    revoke_session(db, session)
    audit.record(
        db,
        action=audit.AUTH_LOGOUT,
        actor_id=actor_id,
        target_type="session",
        target_id=str(session.id),
        ip=ip,
        user_agent=user_agent,
    )
    db.commit()


__all__ = [
    "CSRF_HEADER_NAME",
    "CSRF_TOKEN_BYTES",
    "DEFAULT_SESSION_TTL_HOURS",
    "INVALID_CREDENTIALS_MESSAGE",
    "SESSION_COOKIE_NAME",
    "SESSION_TOKEN_BYTES",
    "as_utc",
    "cookie_secure",
    "create_session",
    "get_active_session",
    "hash_password",
    "hash_session_token",
    "is_session_valid",
    "login",
    "logout",
    "password_needs_rehash",
    "revoke_all_sessions",
    "revoke_session",
    "session_ttl",
    "verify_password",
]
