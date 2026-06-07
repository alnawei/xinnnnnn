from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject
from typing import Any, Union, List

class RoleFilter(BaseFilter):
    """
    权限隔离过滤器 (防呆增强版)。
    使用 kwargs 安全接收外部中间件 (Outer Middleware) 注入的上下文变量。
    """
    def __init__(self, allowed_roles: Union[str, List[str]]):
        # 兼容处理：如果传入的是单字符串 "admin"，自动帮它包装成列表 ["admin"]
        if isinstance(allowed_roles, str):
            self.allowed_roles = [allowed_roles]
        else:
            self.allowed_roles = allowed_roles

    async def __call__(self, event: TelegramObject, **kwargs: Any) -> bool:
        # 安全获取从 AuthMiddleware 注入的 role，默认为 guest 防止提权
        role = kwargs.get("role", "guest")
        return role in self.allowed_roles
