"""认证接口模型。"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """免密登录请求。"""

    username: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class UserResponse(BaseModel):
    """当前登录用户。"""

    user_id: str
    username: str
    role: str


class LogoutResponse(BaseModel):
    """退出登录结果。"""

    success: bool = True
