import asyncio
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from models import EnergyOrder, MicroDepositOrder, ProcessedTx, SaaSOrder, Tenant

PENDING_ORDER_VALID_MINUTES = 10
PROCESSED_TX_RETENTION_DAYS = 10
SPECIAL_ORDER_TIMEOUT_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 60
ORDER_BATCH_SIZE = 500
PROCESSED_TX_BATCH_SIZE = 1000


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


async def expire_and_prune_stale_orders(session: AsyncSession, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    stale_saas_threshold = now - timedelta(minutes=PENDING_ORDER_VALID_MINUTES)
    stats = {
        "micro_deleted": 0,
        "saas_deleted": 0,
    }

    while True:
        stale_ids = (
            await session.execute(
                select(MicroDepositOrder.id)
                .where(
                    MicroDepositOrder.status.in_(["PENDING", "EXPIRED"]),
                    MicroDepositOrder.expired_at < now,
                )
                .limit(ORDER_BATCH_SIZE)
            )
        ).scalars().all()
        if not stale_ids:
            break

        result = await session.execute(delete(MicroDepositOrder).where(MicroDepositOrder.id.in_(stale_ids)))
        await session.commit()
        stats["micro_deleted"] += result.rowcount or 0
        await asyncio.sleep(0.05)

    while True:
        stale_ids = (
            await session.execute(
                select(SaaSOrder.id)
                .where(
                    SaaSOrder.status.in_(["PENDING", "EXPIRED"]),
                    SaaSOrder.created_at < stale_saas_threshold,
                )
                .limit(ORDER_BATCH_SIZE)
            )
        ).scalars().all()
        if not stale_ids:
            break

        result = await session.execute(delete(SaaSOrder).where(SaaSOrder.id.in_(stale_ids)))
        await session.commit()
        stats["saas_deleted"] += result.rowcount or 0
        await asyncio.sleep(0.05)

    return stats


async def finalize_stale_special_orders(session: AsyncSession, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    timeout_threshold = now - timedelta(seconds=SPECIAL_ORDER_TIMEOUT_SECONDS)
    total_closed = 0

    while True:
        stale_orders = (
            await session.execute(
                select(EnergyOrder)
                .where(
                    EnergyOrder.status == "PROCESSING",
                    EnergyOrder.order_type.in_(["DIRECT_SPECIAL", "DIRECT_SPECIAL_65K", "DIRECT_SPECIAL_131K"]),
                    EnergyOrder.created_at < timeout_threshold,
                )
                .limit(ORDER_BATCH_SIZE)
                .with_for_update()
            )
        ).scalars().all()
        if not stale_orders:
            break

        for order in stale_orders:
            refund_amount = _to_decimal(order.admin_base_cost)
            if order.tenant_id and order.tenant_id > 0 and refund_amount > 0:
                tenant = await session.scalar(
                    select(Tenant).where(Tenant.id == order.tenant_id).with_for_update()
                )
                if tenant:
                    tenant.deposit_balance = _to_decimal(tenant.deposit_balance) + refund_amount
                    order.status = "FAILED_REFUNDED"
                    continue
                logging.error(f"❌ [Cleanup Cron] 特价超时订单 #{order.id} 找不到租户 #{order.tenant_id}，改为静默关闭。")

            order.status = "FAILED_SILENT"

        await session.commit()
        total_closed += len(stale_orders)
        await asyncio.sleep(0.05)

    return total_closed


async def purge_processed_tx_history(session: AsyncSession, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    threshold_time = now - timedelta(days=PROCESSED_TX_RETENTION_DAYS)
    deleted_count = 0

    while True:
        stale_hashes = (
            await session.execute(
                select(ProcessedTx.tx_hash)
                .where(ProcessedTx.created_at < threshold_time)
                .limit(PROCESSED_TX_BATCH_SIZE)
            )
        ).scalars().all()
        if not stale_hashes:
            break

        result = await session.execute(delete(ProcessedTx).where(ProcessedTx.tx_hash.in_(stale_hashes)))
        await session.commit()
        deleted_count += result.rowcount or 0
        await asyncio.sleep(0.05)

    return deleted_count

async def run_cleanup_cron(session_maker):
    """
    自动清理失效待支付单、过期防重放哈希、特价超时处理中订单
    """
    logging.info("🧹 [Cleanup Cron] 自动清理任务已后台启动...")

    while True:
        try:
            async with session_maker() as session:
                now = datetime.utcnow()
                order_stats = await expire_and_prune_stale_orders(session, now=now)
                timeout_closed = await finalize_stale_special_orders(session, now=now)
                processed_deleted = await purge_processed_tx_history(session, now=now)

            if any(order_stats.values()) or timeout_closed or processed_deleted:
                logging.info(
                    "🧹 [Cleanup Cron] 本轮完成："
                    f"微充删除 {order_stats['micro_deleted']} 条，"
                    f"SaaS 删除 {order_stats['saas_deleted']} 条，"
                    f"特价超时关闭 {timeout_closed} 条，"
                    f"processed_txs 删除 {processed_deleted} 条。"
                )

        except Exception as e:
            logging.error(f"❌ [Cleanup Cron] 清理任务发生异常: {str(e)}", exc_info=True)

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
