from datetime import datetime
from decimal import Decimal

import pytest

from business_flow_utils import (
    apply_micro_deposit,
    apply_saas_payment,
    confirm_manual_review_success,
    expires_in,
    mark_energy_order_manual_review,
    refund_processing_energy_order,
)
from models import EnergyOrder, MicroDepositOrder, SaaSOrder, Tenant, User


async def create_tenant_user(session, tenant_id=1, owner_tg_id=1001, user_tg_id=2002):
    tenant = Tenant(
        id=tenant_id,
        owner_tg_id=owner_tg_id,
        bot_token=f"{tenant_id}:test-token",
        deposit_balance=Decimal("0"),
        profit_balance=Decimal("0"),
        is_active=True,
        expire_time=datetime.utcnow()
    )
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        tg_user_id=user_tg_id,
        tg_first_name="tester",
        balance=Decimal("0"),
        total_orders=0,
        total_spent_trx=Decimal("0")
    )
    session.add(user)
    await session.flush()
    return tenant, user


@pytest.mark.asyncio
async def test_micro_deposit_credits_user_balance(db_session):
    tenant, user = await create_tenant_user(db_session)
    order = MicroDepositOrder(
        tenant_id=tenant.id,
        user_id=user.id,
        base_amount=10,
        fractional_amount=Decimal("0.125"),
        expected_amount=Decimal("10.125"),
        status="PENDING",
        expired_at=expires_in()
    )
    db_session.add(order)
    await db_session.commit()

    matched = await apply_micro_deposit(db_session, "tx-user-deposit", Decimal("10.125"))
    await db_session.commit()

    assert matched.id == order.id
    assert matched.status == "SUCCESS"
    assert user.balance == Decimal("10.125")


@pytest.mark.asyncio
async def test_micro_deposit_rejects_duplicate_tx_hash(db_session):
    _, user = await create_tenant_user(db_session)
    order = MicroDepositOrder(
        tenant_id=1,
        user_id=user.id,
        base_amount=10,
        fractional_amount=Decimal("0.125"),
        expected_amount=Decimal("10.125"),
        status="PENDING",
        expired_at=expires_in()
    )
    db_session.add(order)
    await db_session.commit()

    await apply_micro_deposit(db_session, "same-tx", Decimal("10.125"))
    await db_session.commit()
    second = await apply_micro_deposit(db_session, "same-tx", Decimal("10.125"))
    await db_session.commit()

    assert second is None
    assert user.balance == Decimal("10.125")


@pytest.mark.asyncio
async def test_duplicate_amount_can_only_credit_one_pending_order(db_session):
    tenant_a, user_a = await create_tenant_user(db_session, tenant_id=1, owner_tg_id=1001, user_tg_id=2001)
    tenant_b, user_b = await create_tenant_user(db_session, tenant_id=2, owner_tg_id=1002, user_tg_id=2002)
    first = MicroDepositOrder(
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        base_amount=10,
        fractional_amount=Decimal("0.125"),
        expected_amount=Decimal("10.125"),
        status="PENDING",
        expired_at=expires_in()
    )
    second = MicroDepositOrder(
        tenant_id=tenant_b.id,
        user_id=user_b.id,
        base_amount=10,
        fractional_amount=Decimal("0.125"),
        expected_amount=Decimal("10.125"),
        status="PENDING",
        expired_at=expires_in()
    )
    db_session.add_all([first, second])
    await db_session.commit()

    matched = await apply_micro_deposit(db_session, "tx-one-credit", Decimal("10.125"))
    await db_session.commit()

    assert matched.id == first.id
    assert user_a.balance == Decimal("10.125")
    assert user_b.balance == Decimal("0.000000")
    assert second.status == "PENDING"


@pytest.mark.asyncio
async def test_saas_payment_marks_order_paid(db_session):
    order = SaaSOrder(
        tg_user_id=1001,
        order_type="clone",
        days="30",
        price=Decimal("29.90"),
        status="PENDING"
    )
    db_session.add(order)
    await db_session.commit()

    matched = await apply_saas_payment(db_session, "tx-saas", Decimal("29.90"))
    await db_session.commit()

    assert matched.id == order.id
    assert order.status == "PAID"


@pytest.mark.asyncio
async def test_processing_order_refund_is_idempotent_by_status(db_session):
    tenant, user = await create_tenant_user(db_session)
    user.balance = Decimal("0")
    user.total_orders = 1
    user.total_spent_trx = Decimal("10")
    order = EnergyOrder(
        tenant_id=tenant.id,
        user_id=user.id,
        order_type="BALANCE_65K",
        target_address="T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
        total_user_deducted=Decimal("10"),
        tenant_markup=Decimal("1"),
        status="PROCESSING"
    )
    db_session.add(order)
    await db_session.commit()

    first = await refund_processing_energy_order(db_session, order.id)
    await db_session.commit()
    second = await refund_processing_energy_order(db_session, order.id)
    await db_session.commit()

    assert first.status == "FAILED_REFUNDED"
    assert second is None
    assert user.balance == Decimal("10.000000")
    assert user.total_orders == 0
    assert user.total_spent_trx == Decimal("0.000000")


@pytest.mark.asyncio
async def test_uncertain_dispatch_moves_to_manual_review(db_session):
    tenant, user = await create_tenant_user(db_session)
    order = EnergyOrder(
        tenant_id=tenant.id,
        user_id=user.id,
        order_type="BALANCE_65K",
        target_address="T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
        total_user_deducted=Decimal("10"),
        status="PROCESSING"
    )
    db_session.add(order)
    await db_session.commit()

    updated = await mark_energy_order_manual_review(db_session, order.id)
    await db_session.commit()

    assert updated.status == "MANUAL_REVIEW"


@pytest.mark.asyncio
async def test_manual_review_success_books_profit(db_session):
    tenant, user = await create_tenant_user(db_session)
    order = EnergyOrder(
        tenant_id=tenant.id,
        user_id=user.id,
        order_type="BALANCE_65K",
        target_address="T9yD14Nj9j7xAB4dbGeiX9h8unkKHxuWwb",
        total_user_deducted=Decimal("10"),
        tenant_markup=Decimal("1.5"),
        status="MANUAL_REVIEW"
    )
    db_session.add(order)
    await db_session.commit()

    updated = await confirm_manual_review_success(db_session, order.id)
    await db_session.commit()

    assert updated.status == "SUCCESS"
    assert tenant.profit_balance == Decimal("1.500000")
