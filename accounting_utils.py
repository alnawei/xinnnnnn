from decimal import Decimal


def to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def mark_order_refunded(order) -> Decimal:
    refund_amount = to_decimal(order.total_user_deducted)
    order.status = "FAILED_REFUNDED"
    return refund_amount


def refund_user_balance(user, refund_amount: Decimal) -> None:
    user.balance = to_decimal(user.balance) + refund_amount
    user.total_orders = max((user.total_orders or 0) - 1, 0)
    spent = to_decimal(user.total_spent_trx) - refund_amount
    user.total_spent_trx = spent if spent > 0 else Decimal("0")


def mark_manual_review_success(order, tenant=None) -> None:
    order.status = "SUCCESS"
    tenant_markup = to_decimal(getattr(order, "tenant_markup", 0))
    if tenant is not None and tenant_markup > 0:
        tenant.profit_balance = to_decimal(tenant.profit_balance) + tenant_markup
