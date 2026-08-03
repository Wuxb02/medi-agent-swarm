"""登录认证与权限依赖。"""

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, Response, status

from mediZJ.memory.session_db import SessionDB


COOKIE_NAME = "medizj_session"
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AuthService:
    """基于 SQLite 随机会话令牌的免密登录服务。"""

    def __init__(self, db: Optional[SessionDB] = None):
        self.db = db or SessionDB()

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def login(self, username: str) -> tuple[Dict[str, Any], str, datetime]:
        """登录或自动创建用户，并返回原始会话令牌。"""

        username = username.strip()
        if not _USERNAME_PATTERN.fullmatch(username):
            raise ValueError("用户名仅允许字母、数字、下划线和连字符，长度 1-64")

        admin_username = os.getenv("MEDIZJ_ADMIN_USERNAME", "admin")
        role = (
            "admin"
            if username.casefold() == admin_username.casefold()
            else "user"
        )
        user = self.db.get_or_create_user(username=username, role=role)
        if not user.get("is_active", 1):
            raise PermissionError("用户已被停用")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=int(os.getenv("AUTH_SESSION_DAYS", "7"))
        )
        self.db.save_auth_session(
            token_hash=self._hash_token(token),
            user_id=user["user_id"],
            expires_at=expires_at.isoformat(),
        )
        return user, token, expires_at

    def authenticate(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """校验 Cookie 令牌，返回当前用户。"""

        if not token:
            return None
        token_hash = self._hash_token(token)
        auth_session = self.db.get_auth_session(token_hash)
        if auth_session is None:
            return None
        if not auth_session.get("is_active"):
            self.db.delete_auth_session(token_hash)
            return None
        expires_at = datetime.fromisoformat(auth_session["expires_at"])
        now = (
            datetime.now(timezone.utc)
            if expires_at.tzinfo is not None
            else datetime.now()
        )
        if expires_at <= now:
            self.db.delete_auth_session(token_hash)
            return None
        return {
            "user_id": auth_session["user_id"],
            "username": auth_session["username"],
            "role": auth_session["role"],
        }

    def logout(self, token: Optional[str]) -> None:
        """撤销当前登录令牌。"""

        if token:
            self.db.delete_auth_session(self._hash_token(token))


_auth_service = AuthService()


def get_auth_service() -> AuthService:
    """提供认证服务，便于测试替换。"""

    return _auth_service


def get_current_user(request: Request) -> Dict[str, Any]:
    """从请求状态读取认证中间件解析出的用户。"""

    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    return user


def require_admin(
    user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """仅允许管理员访问。"""

    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


def set_auth_cookie(
    response: Response,
    token: str,
    expires_at: datetime,
) -> None:
    """设置安全登录 Cookie。"""

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        expires=expires_at,
        httponly=True,
        secure=os.getenv("AUTH_COOKIE_SECURE", "false").lower()
        in {"1", "true", "yes"},
        samesite="strict",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    """删除登录 Cookie。"""

    response.delete_cookie(key=COOKIE_NAME, path="/")
