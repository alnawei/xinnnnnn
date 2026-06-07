import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

# 导入路由和鉴权中间件
from routers.user import get_user_router
from middlewares.auth import AuthMiddleware

class SaaSBotManager:
    """SaaS 多租户机器人动态调度中心 (强沙盒隔离版)"""
    
    def __init__(self):
        # 记录每个租户独有的 Bot, 独立任务, 以及独立的 Dispatcher
        self._running_bots: dict[int, tuple[Bot, asyncio.Task, Dispatcher]] = {}

    async def _safe_polling(self, dp: Dispatcher, bot: Bot, tenant_id: int):
        """【防御堡垒】：沙盒化隔离轮询，防止单节点 401 崩溃引发全站雪崩"""
        try:
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            logging.error(f"🚨 [沙盒隔离] 租户 #{tenant_id} 的机器人发生致命崩溃或被官方封禁: {e}")
        finally:
            logging.info(f"🧹 租户 #{tenant_id} 轮询任务已完全退出沙盒。")

    async def mount_bot(self, tenant_id: int, bot_token: str) -> bool:
        """动态挂载新机器人 (物理级内存隔离)"""
        if tenant_id in self._running_bots:
            return False

        try:
            new_bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode='HTML'))
            bot_info = await new_bot.get_me()
            
            # 🛡️ 防御 1：为每个机器人分配绝对隔离的 Dispatcher 和 独立内存存储！
            # 彻底杜绝多个 Bot 之间发生 FSM 状态串线与“灵魂互换”
            new_dp = Dispatcher(storage=MemoryStorage())
            new_dp.message.outer_middleware(AuthMiddleware())
            new_dp.callback_query.outer_middleware(AuthMiddleware())
            new_dp.include_router(get_user_router())

            logging.info(f"🚀 动态挂载隔离实例: @{bot_info.username} (Tenant ID: {tenant_id})")

            # 🛡️ 防御 2：将轮询放入沙盒拦截器中启动
            polling_task = asyncio.create_task(self._safe_polling(new_dp, new_bot, tenant_id))

            self._running_bots[tenant_id] = (new_bot, polling_task, new_dp)
            return True

        except Exception as e:
            logging.error(f"❌ 挂载租户 {tenant_id} 机器人失败: {e}")
            return False

    async def unmount_bot(self, tenant_id: int):
        """动态卸载机器人 (内存安全释放)"""
        if tenant_id not in self._running_bots:
            return

        # 安全解构提取该租户的独立资产
        bot, polling_task, dp = self._running_bots.pop(tenant_id)
        try:
            polling_task.cancel()
            await bot.session.close()
            logging.info(f"✅ 租户 {tenant_id} 机器人已安全下线并释放沙盒内存。")
        except Exception as e:
            logging.error(f"⚠️ 卸载租户 {tenant_id} 发生异常: {e}")

bot_manager = SaaSBotManager()
