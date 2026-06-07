from typing import Callable, Dict, Any, Awaitable
from datetime import datetime
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from sqlalchemy import select

from models import AsyncSessionLocal, Tenant, User, SystemConfig

class AuthMiddleware(BaseMiddleware):
    """
    全局身份鉴权与核级风控拦截中间件 (强类型隔离版)
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user_id = event.from_user.id
        bot_token = data["bot"].token
        
        role = "guest"
        current_tenant = None
        current_user = None

        async with AsyncSessionLocal() as session:
            # 🛡️ 防御 3：实时动态读取超管ID，并强制转换为去空格字符串，彻底杜绝类型隐式转换造成的降权
            sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
            super_admin_id_str = str(sys_config.super_admin_tg_id).strip() if sys_config and sys_config.super_admin_tg_id else None
            
            # 当前用户的安全字符串 ID
            user_id_str = str(user_id).strip()

            # ================= 1. 查明当前互动的机器人身份 =================
            tenant_bot_stmt = select(Tenant).where(Tenant.bot_token == bot_token)
            bot_tenant_owner = await session.scalar(tenant_bot_stmt)
            
            is_master_bot = (bot_tenant_owner is None)

            if not is_master_bot:
                # ------ 场景 A：当前是【租户克隆机器人】 ------

                # [核级生命周期拦截]
                now = datetime.utcnow()
                if bot_tenant_owner.expire_time < now:
                    if isinstance(event, Message):
                        await event.answer("⚠️ <b>店铺打烊通知</b>\n\n抱歉，本机器人服务授权已到期，各项功能已全面暂停。\n请联系店长完成续费后恢复使用。", parse_mode="HTML")
                    return 

                # [核级封禁拦截]
                if getattr(bot_tenant_owner, "is_banned", False):
                    if isinstance(event, Message):
                        await event.answer("⚠️ <b>服务暂停通知</b>\n\n抱歉，本机器人因违反平台风控规则已被强制关停。", parse_mode="HTML")
                    return 

                current_tenant = bot_tenant_owner
                
                # 自动为访客建立/查询 C 端散客档案
                user_stmt = select(User).where(
                    User.tenant_id == bot_tenant_owner.id, 
                    User.tg_user_id == user_id
                )
                current_user = await session.scalar(user_stmt)
                
                if not current_user:
                    current_user = User(
                        tenant_id=bot_tenant_owner.id,
                        tg_user_id=user_id,
                        tg_first_name=event.from_user.first_name[:120] if event.from_user.first_name else "Unknown"
                    )
                    session.add(current_user)
                    await session.commit()
                    
                # 🛡️ 严格身份鉴定 (强类型字符串比对)
                if super_admin_id_str and user_id_str == super_admin_id_str:
                    role = "admin"
                elif user_id_str == str(bot_tenant_owner.owner_tg_id).strip():
                    role = "tenant"
                else:
                    role = "user"
                    
            else:
                # ------ 场景 B：当前是【SaaS 母平台机器人】 ------
                # 🛡️ 严格身份鉴定
                if super_admin_id_str and user_id_str == super_admin_id_str:
                    role = "admin"
                else:
                    owner_stmt = select(Tenant).where(Tenant.owner_tg_id == user_id)
                    my_tenant = await session.scalar(owner_stmt)
                    if my_tenant:
                        role = "tenant"
                        current_tenant = my_tenant
                    else:
                        role = "guest"

            # ================= 2. 注入上下文并放行 =================
            data["role"] = role
            data["current_tenant"] = current_tenant
            data["current_user"] = current_user
            data["session"] = session

            return await handler(event, data)
