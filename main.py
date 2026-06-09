import asyncio
import logging
import netts_api
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from sqlalchemy import select
from middlewares.auth import AuthMiddleware
# 👇 【修复重点3】：主机器人只导入 admin 和 tenant 的路由
from routers.admin import admin_router
from routers.tenant import tenant_router, tenant_activation_router  # 👈 这里加一下导入
from bot_manager import bot_manager
from cron_jobs import start_scheduler
from tasks import auto_update_netts_price  # 👈 导入我们刚才写好的守护协程
from routers.user import get_user_router  
from services.monitor_task import run_financial_monitor
from tron_scanner import run_scanner
# 导入您项目中的数据库会话工厂 (确保名称匹配 models.py)
from decimal import Decimal
from models import AsyncSessionLocal, Tenant, SystemConfig
from config import MASTER_BOT_TOKEN, SUPER_ADMIN_ID
from models import engine # 请导入你 models.py 里的 engine 变量


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def init_system_config():
    """💡 核心：系统全局配置自动化自检与超管双向同步引擎"""
    logging.info("⚙️ 正在执行系统全局配置自检与超管防线核对...")
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(SystemConfig).where(SystemConfig.id == 1)
            config = (await session.execute(stmt)).scalar_one_or_none()
            
            # 兜底规范地址（对齐 nullable=False 约束）
            dummy_address = "T111111111111111111111111111111111"
            local_super_admin_id = str(SUPER_ADMIN_ID).strip()
            
            if not config:
                logging.warning("⚠️ [自检引擎] 检测到全新部署环境，正在自动初始化全局配置...")
                config = SystemConfig(
                    id=1,
                    master_receive_address=dummy_address, # 填入兜底地址防崩溃
                    netts_alert_threshold=Decimal("50.00"),
                    tenant_alert_threshold=Decimal("15.00"),
                    super_admin_tg_id=local_super_admin_id # 自动注入代码里的超管ID
                )
                session.add(config)
                logging.info(f"✅ [自检引擎] 全新环境初始化成功！已自动绑定超管专线: {local_super_admin_id}")
            else:
                # 💡 核心强化：动态覆盖与对齐。代码配置优先级最高！
                current_db_id = getattr(config, 'super_admin_tg_id', None)
                if str(current_db_id) != local_super_admin_id:
                    logging.info(f"🔄 [自检引擎] 检测到本地超管配置变更 (DB:{current_db_id} -> Local:{local_super_admin_id})，正在自动同步至数据库...")
                    config.super_admin_tg_id = local_super_admin_id
                    logging.info(f"✅ [自检引擎] 超管专线已自动同步更新为: {local_super_admin_id}")
                else:
                    logging.info("✅ [自检引擎] 数据库与本地超管专线校验通过。")
            
            await session.commit()
            
    except Exception as e:
        logging.error(f"❌ [自检引擎] 系统配置自动初始化或同步失败: {e}")
        
async def startup_all_active_bots():
    logging.info("🔄 正在从数据库加载存活的租户机器人矩阵...")
    async with AsyncSessionLocal() as session:
        stmt = select(Tenant).where(Tenant.is_active == 1)
        active_tenants = (await session.scalars(stmt)).all()
        
    success_count = 0
    for t in active_tenants:
        is_success = await bot_manager.mount_bot(t.id, t.bot_token)
        if is_success:
            success_count += 1
        await asyncio.sleep(0.1) 
    
    logging.info(f"✅ 成功挂载 {success_count} 个租户子机器人！")



async def main():
    logging.info("🚀=========================================🚀")
    logging.info("    Telegram 多租户能量 SaaS 平台正在启动...")
    logging.info("🚀=========================================🚀")

    
    # 0. 数据库配置自检与底层初始化 (必须第一步执行)
    await init_system_config()

    # 1. 启动生命周期管理
    start_scheduler()
    await startup_all_active_bots()
    # ... 保留原有的其余启动代码 ...
    # 使用 create_task 提交到 asyncio 循环中静默运行，不要加 await (防阻塞)
    asyncio.create_task(auto_update_netts_price(AsyncSessionLocal))
    # 2. 初始化母平台机器人
    master_bot = Bot(token=MASTER_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    master_dp = Dispatcher()
    
    master_dp.message.outer_middleware(AuthMiddleware())
    master_dp.callback_query.outer_middleware(AuthMiddleware())
    
    # 👇 【修复重点4】：主机器人专职接待管理层，只挂载这俩！
    master_dp.include_router(admin_router)
    master_dp.include_router(tenant_router)
    master_dp.include_router(tenant_activation_router)
    
    # 🐛 [Bug 2 修复的最核心]：把散客入口也挂载给母平台！否则 /start 会直接被丢弃！
    master_dp.include_router(get_user_router())  

    logging.info("👑 母平台机器人主进程已就绪，开始接收消息...")
    
    # ⚠️ 点火挂载：全局财务报警雷达 (成功注入 Netts API 模块)
    monitor_task = asyncio.create_task(run_financial_monitor(AsyncSessionLocal, master_bot, netts_api))
    scanner_task = asyncio.create_task(run_scanner(master_bot, AsyncSessionLocal))
    try:
        # 启动机器人轮询 (非阻塞地与 scanner_task 并发运行)
        await master_dp.start_polling(master_bot)
    finally:
        logging.info("🧹 正在执行优雅退出...")
        
        # 🛡️ SRE 加固：全面回收所有后台常驻协程，杜绝孤儿任务导致内存溢出
        for task_name in ['monitor_task', 'scanner_task']:
            if task_name in locals() and not locals()[task_name].done():
                locals()[task_name].cancel()
                
        await master_bot.session.close()
        
        # 2. 优雅关闭所有子机器人及其轮询任务 (严格的 4 空格对齐)
        for tenant_id, (sub_bot, sub_task) in bot_manager._running_bots.items():
            await sub_bot.session.close()
            if sub_task:
                sub_task.cancel()
                
        logging.info("💤 系统已安全关停。")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("🛑 收到用户强制退出信号！")
