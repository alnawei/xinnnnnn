import asyncio
import logging
from decimal import Decimal
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from models import EnergyOrder, FinancialDailySummary, MicroDepositOrder, ProcessedTx, SaaSOrder, Tenant, WithdrawOrder

PENDING_ORDER_VALID_MINUTES = 10
PROCESSED_TX_RETENTION_DAYS = 10
FINANCIAL_DETAIL_RETENTION_DAYS = 10
FINANCIAL_SUMMARY_RETENTION_DAYS = 31
SPECIAL_ORDER_TIMEOUT_SECONDS = 60
CLEANUP_INTERVAL_SECONDS = 60
ORDER_BATCH_SIZE = 500
PROCESSED_TX_BATCH_SIZE = 1000


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _day_bounds(summary_date: date) -> tuple[datetime, datetime]:
    start_time = datetime.combine(summary_date, datetime.min.time())
    return start_time, start_time + timedelta(days=1)


async def _get_or_create_summary(session: AsyncSession, summary_date: date, tenant_id: int) -> FinancialDailySummary:
    summary = await session.scalar(
        select(FinancialDailySummary).where(
            FinancialDailySummary.summary_date == summary_date,
            FinancialDailySummary.tenant_id == tenant_id,
        ).with_for_update()
    )
    if summary:
        return summary

    summary = FinancialDailySummary(summary_date=summary_date, tenant_id=tenant_id)
    session.add(summary)
    await session.flush()
    return summary


async def rollup_financial_details_for_day(session: AsyncSession, summary_date: date) -> int:
    existing_summary_count = await session.scalar(
        select(func.count(FinancialDailySummary.id)).where(FinancialDailySummary.summary_date == summary_date)
    )
    if existing_summary_count:
        return 0

    start_time, end_time = _day_bounds(summary_date)
    touched = 0

    deposit_rows = (
        await session.execute(
            select(
                MicroDepositOrder.tenant_id,
                func.count(MicroDepositOrder.id),
                func.coalesce(func.sum(MicroDepositOrder.expected_amount), 0),
            )
            .where(
                MicroDepositOrder.status == "SUCCESS",
                MicroDepositOrder.created_at >= start_time,
                MicroDepositOrder.created_at < end_time,
            )
            .group_by(MicroDepositOrder.tenant_id)
        )
    ).all()
    for tenant_id, count, total in deposit_rows:
        summary = await _get_or_create_summary(session, summary_date, int(tenant_id or 0))
        summary.deposit_success_count = int(count or 0)
        summary.deposit_trx = _to_decimal(total)
        touched += 1

    energy_rows = (
        await session.execute(
            select(
                EnergyOrder.tenant_id,
                func.sum(case((EnergyOrder.status == "SUCCESS", 1), else_=0)),
                func.sum(case((EnergyOrder.status == "FAILED_REFUNDED", 1), else_=0)),
                func.sum(case((EnergyOrder.status == "FAILED_SILENT", 1), else_=0)),
                func.coalesce(func.sum(case((EnergyOrder.status == "SUCCESS", EnergyOrder.total_user_deducted), else_=0)), 0),
                func.coalesce(func.sum(case((EnergyOrder.status == "FAILED_REFUNDED", EnergyOrder.total_user_deducted), else_=0)), 0),
                func.coalesce(func.sum(case((EnergyOrder.status == "SUCCESS", EnergyOrder.admin_base_cost), else_=0)), 0),
                func.coalesce(func.sum(case((EnergyOrder.status == "SUCCESS", EnergyOrder.tenant_markup), else_=0)), 0),
            )
            .where(
                EnergyOrder.status.in_(["SUCCESS", "FAILED_REFUNDED", "FAILED_SILENT"]),
                EnergyOrder.created_at >= start_time,
                EnergyOrder.created_at < end_time,
            )
            .group_by(EnergyOrder.tenant_id)
        )
    ).all()
    for row in energy_rows:
        tenant_id, success_count, refund_count, failed_count, paid_trx, refund_trx, admin_cost, tenant_profit = row
        summary = await _get_or_create_summary(session, summary_date, int(tenant_id or 0))
        summary.energy_success_count = int(success_count or 0)
        summary.energy_refund_count = int(refund_count or 0)
        summary.energy_failed_count = int(failed_count or 0)
        summary.energy_user_paid_trx = _to_decimal(paid_trx)
        summary.energy_refund_trx = _to_decimal(refund_trx)
        summary.admin_cost_trx = _to_decimal(admin_cost)
        summary.tenant_profit_trx = _to_decimal(tenant_profit)
        touched += 1

    withdraw_rows = (
        await session.execute(
            select(
                WithdrawOrder.tenant_id,
                func.sum(case((WithdrawOrder.status == "PAID", 1), else_=0)),
                func.coalesce(func.sum(case((WithdrawOrder.status == "PAID", WithdrawOrder.amount), else_=0)), 0),
                func.sum(case((WithdrawOrder.status == "REJECTED", 1), else_=0)),
                func.coalesce(func.sum(case((WithdrawOrder.status == "REJECTED", WithdrawOrder.amount), else_=0)), 0),
            )
            .where(
                WithdrawOrder.status.in_(["PAID", "REJECTED"]),
                WithdrawOrder.created_at >= start_time,
                WithdrawOrder.created_at < end_time,
            )
            .group_by(WithdrawOrder.tenant_id)
        )
    ).all()
    for tenant_id, paid_count, paid_trx, rejected_count, rejected_trx in withdraw_rows:
        summary = await _get_or_create_summary(session, summary_date, int(tenant_id or 0))
        summary.withdraw_paid_count = int(paid_count or 0)
        summary.withdraw_paid_trx = _to_decimal(paid_trx)
        summary.withdraw_rejected_count = int(rejected_count or 0)
        summary.withdraw_rejected_trx = _to_decimal(rejected_trx)
        touched += 1

    saas_rows = (
        await session.execute(
            select(
                func.count(SaaSOrder.id),
                func.coalesce(func.sum(SaaSOrder.price), 0),
            )
            .where(
                (
                    (SaaSOrder.status == "ACTIVATED")
                    | ((SaaSOrder.status == "PAID") & (SaaSOrder.order_type == "special"))
                ),
                SaaSOrder.created_at >= start_time,
                SaaSOrder.created_at < end_time,
            )
        )
    ).one()
    saas_count, saas_usdt = saas_rows
    if saas_count:
        summary = await _get_or_create_summary(session, summary_date, 0)
        summary.saas_paid_count = int(saas_count or 0)
        summary.saas_paid_usdt = _to_decimal(saas_usdt)
        touched += 1

    return touched


async def rollup_and_prune_financial_details(session: AsyncSession, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    cutoff_day = (now - timedelta(days=FINANCIAL_DETAIL_RETENTION_DAYS)).date()
    stats = {
        "summary_days": 0,
        "summary_rows": 0,
        "micro_deleted": 0,
        "energy_deleted": 0,
        "withdraw_deleted": 0,
        "saas_deleted": 0,
        "summary_deleted": 0,
    }

    oldest_candidates = [
        await session.scalar(
            select(func.min(MicroDepositOrder.created_at)).where(MicroDepositOrder.status == "SUCCESS")
        ),
        await session.scalar(
            select(func.min(EnergyOrder.created_at)).where(
                EnergyOrder.status.in_(["SUCCESS", "FAILED_REFUNDED", "FAILED_SILENT"])
            )
        ),
        await session.scalar(
            select(func.min(WithdrawOrder.created_at)).where(WithdrawOrder.status.in_(["PAID", "REJECTED"]))
        ),
        await session.scalar(
            select(func.min(SaaSOrder.created_at)).where(
                SaaSOrder.status.in_(["ACTIVATED", "PAID"]),
                SaaSOrder.order_type == "special",
            )
        ),
        await session.scalar(
            select(func.min(SaaSOrder.created_at)).where(
                SaaSOrder.status == "ACTIVATED",
                SaaSOrder.order_type == "clone",
            )
        ),
    ]
    oldest = min((dt for dt in oldest_candidates if dt is not None), default=None)
    if oldest:
        current_day = oldest.date()
        while current_day < cutoff_day:
            touched = await rollup_financial_details_for_day(session, current_day)
            await session.commit()
            if touched:
                stats["summary_days"] += 1
                stats["summary_rows"] += touched
            current_day += timedelta(days=1)
            await asyncio.sleep(0.05)

    detail_cutoff = datetime.combine(cutoff_day, datetime.min.time())
    delete_specs = [
        (
            "micro_deleted",
            MicroDepositOrder,
            MicroDepositOrder.status == "SUCCESS",
            MicroDepositOrder.created_at < detail_cutoff,
        ),
        (
            "energy_deleted",
            EnergyOrder,
            EnergyOrder.status.in_(["SUCCESS", "FAILED_REFUNDED", "FAILED_SILENT"]),
            EnergyOrder.created_at < detail_cutoff,
        ),
        (
            "withdraw_deleted",
            WithdrawOrder,
            WithdrawOrder.status.in_(["PAID", "REJECTED"]),
            WithdrawOrder.created_at < detail_cutoff,
        ),
        (
            "saas_deleted",
            SaaSOrder,
            (
                (SaaSOrder.status == "ACTIVATED")
                | ((SaaSOrder.status == "PAID") & (SaaSOrder.order_type == "special"))
            ),
            SaaSOrder.created_at < detail_cutoff,
        ),
    ]

    for key, model, status_condition, time_condition in delete_specs:
        while True:
            ids = (
                await session.execute(
                    select(model.id)
                    .where(status_condition, time_condition)
                    .limit(ORDER_BATCH_SIZE)
                )
            ).scalars().all()
            if not ids:
                break

            result = await session.execute(delete(model).where(model.id.in_(ids)))
            await session.commit()
            stats[key] += result.rowcount or 0
            await asyncio.sleep(0.05)

    summary_cutoff = now.date() - timedelta(days=FINANCIAL_SUMMARY_RETENTION_DAYS)
    while True:
        ids = (
            await session.execute(
                select(FinancialDailySummary.id)
                .where(FinancialDailySummary.summary_date < summary_cutoff)
                .limit(ORDER_BATCH_SIZE)
            )
        ).scalars().all()
        if not ids:
            break

        result = await session.execute(delete(FinancialDailySummary).where(FinancialDailySummary.id.in_(ids)))
        await session.commit()
        stats["summary_deleted"] += result.rowcount or 0
        await asyncio.sleep(0.05)

    return stats


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
                financial_stats = await rollup_and_prune_financial_details(session, now=now)

            if any(order_stats.values()) or timeout_closed or processed_deleted or any(financial_stats.values()):
                logging.info(
                    "🧹 [Cleanup Cron] 本轮完成："
                    f"微充删除 {order_stats['micro_deleted']} 条，"
                    f"SaaS 删除 {order_stats['saas_deleted']} 条，"
                    f"特价超时关闭 {timeout_closed} 条，"
                    f"processed_txs 删除 {processed_deleted} 条，"
                    f"汇总天数 {financial_stats['summary_days']} 天，"
                    f"明细删除 {financial_stats['micro_deleted'] + financial_stats['energy_deleted'] + financial_stats['withdraw_deleted'] + financial_stats['saas_deleted']} 条，"
                    f"旧汇总删除 {financial_stats['summary_deleted']} 条。"
                )

        except Exception as e:
            logging.error(f"❌ [Cleanup Cron] 清理任务发生异常: {str(e)}", exc_info=True)

        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
