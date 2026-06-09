from decimal import Decimal
from types import SimpleNamespace

from accounting_utils import mark_manual_review_success, mark_order_refunded, refund_user_balance


def test_refund_user_balance_restores_amount_and_stats():
    order = SimpleNamespace(total_user_deducted=Decimal("12.50"), status="MANUAL_REVIEW")
    user = SimpleNamespace(balance=Decimal("7.50"), total_orders=1, total_spent_trx=Decimal("12.50"))

    refund_amount = mark_order_refunded(order)
    refund_user_balance(user, refund_amount)

    assert order.status == "FAILED_REFUNDED"
    assert user.balance == Decimal("20.00")
    assert user.total_orders == 0
    assert user.total_spent_trx == Decimal("0")


def test_refund_does_not_make_counters_negative():
    user = SimpleNamespace(balance=Decimal("0"), total_orders=0, total_spent_trx=Decimal("1.00"))

    refund_user_balance(user, Decimal("5.00"))

    assert user.balance == Decimal("5.00")
    assert user.total_orders == 0
    assert user.total_spent_trx == Decimal("0")


def test_manual_review_success_marks_order_and_books_profit():
    order = SimpleNamespace(status="MANUAL_REVIEW", tenant_markup=Decimal("1.25"))
    tenant = SimpleNamespace(profit_balance=Decimal("10.00"))

    mark_manual_review_success(order, tenant)

    assert order.status == "SUCCESS"
    assert tenant.profit_balance == Decimal("11.25")


def test_manual_review_success_without_profit_only_changes_status():
    order = SimpleNamespace(status="MANUAL_REVIEW", tenant_markup=Decimal("0"))
    tenant = SimpleNamespace(profit_balance=Decimal("10.00"))

    mark_manual_review_success(order, tenant)

    assert order.status == "SUCCESS"
    assert tenant.profit_balance == Decimal("10.00")
