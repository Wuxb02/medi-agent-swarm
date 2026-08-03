"""登录、退出和当前用户接口。"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from mediZJ.api.auth import (
    COOKIE_NAME,
    AuthService,
    clear_auth_cookie,
    get_auth_service,
    get_current_user,
    set_auth_cookie,
)
from mediZJ.api.models.auth import LoginRequest, LogoutResponse, UserResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """按用户名免密登录；不存在的普通用户名会自动创建。"""

    try:
        user, token, expires_at = service.login(body.username)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    set_auth_cookie(response, token, expires_at)
    return UserResponse(
        user_id=user["user_id"],
        username=user["username"],
        role=user["role"],
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    """退出并撤销当前会话。"""

    service.logout(request.cookies.get(COOKIE_NAME))
    clear_auth_cookie(response)
    return LogoutResponse()


@router.get("/me", response_model=UserResponse)
async def me(
    user: Dict[str, Any] = Depends(get_current_user),
):
    """返回当前登录用户。"""

    return UserResponse(**user)
