import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
# 请确保导入对应的模型
from models import MicroDepositOrder, SaaSOrder

async def run_cleanup_cron(session_maker):
    """
    SRE 级过期订单标记任务 (分批处理，保留财务审计流水)
    """
    logging.info("🧹 [Cleanup Cron] 过期订单分批标记任务已后台启动...")
    
    while True:
        try:
            threshold_time = datetime.utcnow() - timedelta(hours=24)
            total_micro_expired = 0
            total_saas_expired = 0

            async with session_maker() as session:
                # ========================================================
                # 1. 分批标记微小尾数订单，保留流水便于财务审计
                # ========================================================
                while True:
                    stmt_micro = select(MicroDepositOrder.id).where(
                        MicroDepositOrder.status == 'PENDING',
                        MicroDepositOrder.created_at < threshold_time
                    ).limit(500)
                    stale_ids = (await session.execute(stmt_micro)).scalars().all()
                    
                    if not stale_ids:
                        break  # 清理完毕，跳出当前循环
                        
                    await session.execute(
                        update(MicroDepositOrder)
                        .where(MicroDepositOrder.id.in_(stale_ids), MicroDepositOrder.status == 'PENDING')
                        .values(status='EXPIRED')
                    )
                    await session.commit()  # 提交短事务，释放数据库压力
                    
                    total_micro_expired += len(stale_ids)
                    await asyncio.sleep(0.1)  # 💡 SRE 呼吸时间：让出 CPU 与连接池，保障主业务畅通

                # ========================================================
                # 2. 分批标记 SaaS 订单
                # ========================================================
                while True:
                    stmt_saas = select(SaaSOrder.id).where(
                        SaaSOrder.status == 'PENDING',
                        SaaSOrder.created_at < threshold_time
                    ).limit(500)
                    stale_ids = (await session.execute(stmt_saas)).scalars().all()
                    
                    if not stale_ids:
                        break
                        
                    await session.execute(
                        update(SaaSOrder)
                        .where(SaaSOrder.id.in_(stale_ids), SaaSOrder.status == 'PENDING')
                        .values(status='EXPIRED')
                    )
                    await session.commit()
                    
                    total_saas_expired += len(stale_ids)
                    await asyncio.sleep(0.1)

            total_expired = total_micro_expired + total_saas_expired
            if total_expired > 0:
                logging.info(f"🗑️ [Cleanup Cron] 安全分批标记完毕！共标记 {total_expired} 条过期记录。")

        except Exception as e:
            logging.error(f"❌ [Cleanup Cron] 清理任务发生异常: {str(e)}", exc_info=True)

        # 挂起协程，每 24 小时（86400 秒）执行一次
        await asyncio.sleep(86400)
