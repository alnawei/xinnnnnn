import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete

from models import Tenant, SystemConfig, ActivationCode
from models import AsyncSessionLocal
from bot_manager import bot_manager

async def cleanup_zombie_tenants():
    """僵尸租户与到期租户扫描清理引擎"""
    logging.info("🧹 启动每日自动化扫描：僵尸租户与过期租户清理...")
    
    async with AsyncSessionLocal() as session:
        sys_config = await session.scalar(select(SystemConfig).where(SystemConfig.id == 1))
        zombie_days = sys_config.zombie_tenant_days if sys_config else 30
        
        now = datetime.utcnow()
        zombie_threshold = now - timedelta(days=zombie_days)

        stmt = select(Tenant).where(
            Tenant.is_active == 1,
            (Tenant.expire_time < now) | (Tenant.last_active_time < zombie_threshold)
        )
        target_tenants = (await session.scalars(stmt)).all()

        if not target_tenants:
            logging.info("✨ 今日扫描完毕，暂无需要清理的僵尸/过期租户。")
            return

        for tenant in target_tenants:
         reason = "套餐过期" if tenant.expire_time < now else f"连续 {zombie_days} 天无交互"
         logging.info(f"☠️ 发现僵尸租户 (ID: {tenant.id}), 原因: {reason}。正在执行剥离程序...")
         
         await bot_manager.unmount_bot(tenant.id)
         tenant.is_active = 0
         
         # 🛡️ SRE 加固：加入漏斗节流阀，防并发下线请求瞬间打爆 Telegram API 限流
         await asyncio.sleep(0.5)

        await session.commit()
        logging.info(f"✅ 僵尸清理任务完成，共清理 {len(target_tenants)} 个租户。")

# ==================== 【新增】废弃卡密自动销毁任务 ====================
async def cleanup_expired_unused_codes():
    """清理生成超过 10 天但仍未被使用的废弃激活码"""
    logging.info("🗑️ 启动自动清理：超过 10 天未使用的废弃卡密...")
    
    async with AsyncSessionLocal() as session:
        # 计算 10 天前的时间阈值
        threshold_time = datetime.utcnow() - timedelta(days=10)
        
        # 执行硬删除 (DELETE FROM activation_codes WHERE is_used = False AND created_at < threshold)
        stmt = delete(ActivationCode).where(
            ActivationCode.is_used == False,
            ActivationCode.created_at < threshold_time
        )
        
        result = await session.execute(stmt)
        await session.commit()
        
        if result.rowcount > 0:
            logging.info(f"✅ 废弃卡密清理完成！共硬删除 {result.rowcount} 个过期未使用的激活码。")
        else:
            logging.info("✨ 暂无超过 10 天未使用的激活码。")


def start_scheduler():
    """启动全局定时任务引擎"""
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    
    # 每天凌晨 03:00 执行僵尸租户清理
    scheduler.add_job(cleanup_zombie_tenants, trigger='cron', hour=3, minute=0)
    
    # 【新增】每天凌晨 03:30 执行废弃激活码清理
    scheduler.add_job(cleanup_expired_unused_codes, trigger='cron', hour=3, minute=30)
    
    scheduler.start()
    logging.info("⏰ APScheduler 定时任务引擎已启动。包含：[僵尸租户清理]、[废弃卡密销毁]")
