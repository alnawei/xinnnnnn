# middlewares/auth.py

from typing import Callable, Dict, Any, Awaitable
from datetime import datetime
from aiogram import BaseMiddleware
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from aiogram.types import TelegramObject, Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
# 确保引入 MASTER_BOT_TOKEN（防伪装）和 SAAS_BOT_URL（引流跳转）
from config import SUPER_ADMIN_ID, MASTER_BOT_TOKEN, SAAS_BOT_URL
from models import AsyncSessionLocal, Tenant, User, SystemConfig

class AuthMiddleware(BaseMiddleware):
    """
    全局身份鉴权与核级风控拦截中间件 (强类型隔离与高并发防撞版)
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
            # 🛡️ 防御 1：实时动态读取超管ID，并强制转换为去空格字符串，彻底杜绝类型隐式转换造成的降权
            sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
            super_admin_id_str = str(sys_config.super_admin_tg_id).strip() if sys_config and sys_config.super_admin_tg_id else None
            
            # 当前用户的安全字符串 ID
            user_id_str = str(user_id).strip()

            # ================= 1. 查明当前互动的机器人身份 =================
            # 🛡️ 架构级修复：显式校验 Token 是否与母平台一致，杜绝被删记录的子机器人“越权”成为主平台
            is_master_bot = (bot_token == MASTER_BOT_TOKEN)

            if not is_master_bot:
                # ------ 场景 A：当前是【租户克隆机器人】 ------
                tenant_bot_stmt = select(Tenant).where(Tenant.bot_token == bot_token)
                bot_tenant_owner = await session.scalar(tenant_bot_stmt)
                
                if not bot_tenant_owner:
                    # [兜底防御] 子机器人进程还在，但数据库记录已被永久删除
                    if isinstance(event, Message):
                        await event.answer("⚠️ <b>服务已终止</b>\n该数字商铺已被注销，服务不可用。", parse_mode="HTML")
                    return # 彻底物理阻断
                    
                # 🌟 [核级生命周期拦截 - 过期引流裂变]
                now = datetime.utcnow()
                if bot_tenant_owner.expire_time < now:
                    if isinstance(event, Message):
                        if event.text and event.text.strip() == "🚀 克隆我的机器人":
                            # 阶段二：引流转化弹窗
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🤖 前往 SaaS 主平台开通", url=SAAS_BOT_URL)]
                            ])
                            promo_text = (
                                "✅ <b>独立专属数字分销商铺</b>\n"
                                "✅ <b>自定义高溢价，纯利 100% 归你</b>\n"
                                "✅ <b>对接全网极速低价能量池</b>"
                            )
                            await event.answer(promo_text, reply_markup=kb, parse_mode="HTML")
                        else:
                            # 阶段一：过期拦截提示 (强制锁死底部键盘)
                            kb = ReplyKeyboardMarkup(
                                keyboard=[[KeyboardButton(text="🚀 克隆我的机器人")]], 
                                resize_keyboard=True,
                                is_persistent=True
                            )
                            await event.answer(
                                "⚠️ <b>该机器人的服务期已到期</b>\n\n"
                                "👇 请使用下方菜单栏选择您需要的服务：", 
                                reply_markup=kb, 
                                parse_mode="HTML"
                            )
                    elif isinstance(event, CallbackQuery):
                        await event.answer("⚠️ 机器人服务已到期，请克隆属于您自己的机器人！", show_alert=True)
                        
                    # 彻底阻断向下穿透，杜绝一切路由泄漏与灵魂互换！
                    return 

                # 🛡️ [核级封禁拦截] (保留原逻辑)
                if getattr(bot_tenant_owner, "is_banned", False):
                    if isinstance(event, Message):
                        await event.answer("⚠️ <b>服务暂停通知</b>\n\n抱歉，本机器人因违反平台风控规则已被强制关停。", parse_mode="HTML")
                    return 

                current_tenant = bot_tenant_owner
                
                # ... [保留原有的 current_user 散客建档逻辑与鉴权逻辑] ...
                
                if not current_user:
                    try:
                        current_user = User(
                            tenant_id=bot_tenant_owner.id,
                            tg_user_id=user_id,
                            tg_first_name=event.from_user.first_name[:120] if event.from_user.first_name else "Unknown",
                            balance=0,
                            total_orders=0,
                            total_spent_trx=0
                        )
                        session.add(current_user)
                        await session.commit()
                    except IntegrityError:
                        # 🛡️ 防御 2 (并发防撞击气囊)：捕获唯一键冲突
                        # 说明在极短的毫秒内，其他并发的协程已经帮该用户建好档案了
                        await session.rollback()
                        # 安全回滚后，直接再查一次，平滑拿到最新数据，绝不崩溃！
                        current_user = await session.scalar(user_stmt)
                    
                # 🛡️ 防御 3：严格身份鉴定 (强类型字符串比对)
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
