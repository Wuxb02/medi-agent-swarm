"""可信执行上下文，工具不能由模型参数改变所有者。"""

from contextvars import ContextVar

execution_budget: ContextVar[tuple[float, float] | None] = ContextVar(
    "execution_budget", default=None
)

execution_identity: ContextVar[tuple[str, str] | None] = ContextVar(
    "execution_identity",
    default=None,
)


def get_identity() -> tuple[str, str]:
    identity = execution_identity.get()
    if identity is None:
        raise RuntimeError("工具缺少可信执行上下文")
    return identity
