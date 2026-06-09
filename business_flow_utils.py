from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from accounting_utils import mark_manual_review_success, mark_order_refunded, refund_user_balance
from models import EnergyOrder, MicroDepositOrder, ProcessedTx, SaaSOrder, Tenant, User


async def apply_micro_deposit(session, tx_hash: str, amount: Decimal):
    existing = await session.scalar(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))
    if existing:
        return None

    order = await session.scalar(
        select(MicroDepositOrder)
        .where(
            MicroDepositOrder.expected_amount == amount,
            MicroDepositOrder.status == "PENDING",
            MicroDepositOrder.expired_at > datetime.utcnow()
        )
        .order_by(MicroDepositOrder.created_at.asc())
        .with_for_update()
    )
    if not order:
        return None

    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    tenant = await session.scalar(select(Tenant).where(Tenant.id == order.tenant_id).with_for_update())
    if not user or not tenant:
        return None

    order.status = "SUCCESS"
    if user.tg_user_id == tenant.owner_tg_id:
        tenant.deposit_balance = Decimal(str(tenant.deposit_balance or 0)) + order.expected_amount
    else:
        user.balance = Decimal(str(user.balance or 0)) + order.expected_amount

    session.add(ProcessedTx(tx_hash=tx_hash))
    return order


async def mark_energy_order_manual_review(session, order_id: int):
    order = await session.scalar(
        select(EnergyOrder)
        .where(EnergyOrder.id == order_id, EnergyOrder.status == "PROCESSING")
        .with_for_update()
    )
    if not order:
        return None
    order.status = "MANUAL_REVIEW"
    return order


async def refund_processing_energy_order(session, order_id: int):
    order = await session.scalar(
        select(EnergyOrder)
        .where(EnergyOrder.id == order_id, EnergyOrder.status.in_(["PROCESSING", "MANUAL_REVIEW"]))
        .with_for_update()
    )
    if not order or not order.user_id:
        return None

    user = await session.scalar(select(User).where(User.id == order.user_id).with_for_update())
    if not user:
        return None

    refund_amount = mark_order_refunded(order)
    refund_user_balance(user, refund_amount)
    return order


async def confirm_manual_review_success(session, order_id: int):
    order = await session.scalar(
        select(EnergyOrder)
        .where(EnergyOrder.id == order_id, EnergyOrder.status == "MANUAL_REVIEW")
        .with_for_update()
    )
    if not order:
        return None

    tenant = None
    if order.tenant_markup and order.tenant_markup > 0:
        tenant = await session.scalar(select(Tenant).where(Tenant.id == order.tenant_id).with_for_update())

    mark_manual_review_success(order, tenant)
    return order


async def apply_saas_payment(session, tx_hash: str, amount: Decimal):
    existing = await session.scalar(select(ProcessedTx).where(ProcessedTx.tx_hash == tx_hash))
    if existing:
        return None

    valid_time_limit = datetime.utcnow() - timedelta(minutes=10)
    order = await session.scalar(
        select(SaaSOrder)
        .where(
            SaaSOrder.status == "PENDING",
            SaaSOrder.price == amount,
            SaaSOrder.created_at >= valid_time_limit
        )
        .order_by(SaaSOrder.created_at.asc())
        .with_for_update()
    )
    if not order:
        return None

    int(str(order.days))
    order.status = "PAID"
    session.add(ProcessedTx(tx_hash=tx_hash))
    return order


def expires_in(minutes: int = 10):
    return datetime.utcnow() + timedelta(minutes=minutes)
