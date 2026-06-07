import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
# 请确保导入对应的模型
from models import MicroDepositOrder, SaaSOrder

async def run_cleanup_cron(session_maker):
    """
    SRE 级垃圾订单自动清理任务 (分批处理，绝对防锁表)
    """
    logging.info("🧹 [Cleanup Cron] 垃圾订单分批清理任务已后台启动...")
    
    while True:
        try:
            threshold_time = datetime.utcnow() - timedelta(hours=24)
            total_micro_deleted = 0
            total_saas_deleted = 0

            async with session_maker() as session:
                # ========================================================
                # 1. 分批清理微小尾数订单 (每次限删 500 条，防止长事务锁表)
                # ========================================================
                while True:
                    # 仅查出过期记录的 ID (极其轻量)
                    stmt_micro = select(MicroDepositOrder.id).where(
                        MicroDepositOrder.status == 'PENDING',
                        MicroDepositOrder.created_at < threshold_time
                    ).limit(500)
                    stale_ids = (await session.execute(stmt_micro)).scalars().all()
                    
                    if not stale_ids:
                        break  # 清理完毕，跳出当前循环
                        
                    # 精准按 ID 抹除
                    await session.execute(delete(MicroDepositOrder).where(MicroDepositOrder.id.in_(stale_ids)))
                    await session.commit()  # 提交短事务，释放数据库压力
                    
                    total_micro_deleted += len(stale_ids)
                    await asyncio.sleep(0.1)  # 💡 SRE 呼吸时间：让出 CPU 与连接池，保障主业务畅通

                # ========================================================
                # 2. 分批清理 SaaS 订单
                # ========================================================
                while True:
                    stmt_saas = select(SaaSOrder.id).where(
                        SaaSOrder.status == 'PENDING',
                        SaaSOrder.created_at < threshold_time
                    ).limit(500)
                    stale_ids = (await session.execute(stmt_saas)).scalars().all()
                    
                    if not stale_ids:
                        break
                        
                    await session.execute(delete(SaaSOrder).where(SaaSOrder.id.in_(stale_ids)))
                    await session.commit()
                    
                    total_saas_deleted += len(stale_ids)
                    await asyncio.sleep(0.1)

            total_deleted = total_micro_deleted + total_saas_deleted
            if total_deleted > 0:
                logging.info(f"🗑️ [Cleanup Cron] 安全分批清理完毕！共释放 {total_deleted} 条废弃记录。")

        except Exception as e:
            logging.error(f"❌ [Cleanup Cron] 清理任务发生异常: {str(e)}", exc_info=True)

        # 挂起协程，每 24 小时（86400 秒）执行一次
        await asyncio.sleep(86400)
