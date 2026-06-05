"""Pricing calculations for the eval fixture repo."""

TAX_RATE = 0.2


def calculate_subtotal(items: list) -> float:
    """Sum the price * quantity of every line item."""
    return sum(item["price"] * item["qty"] for item in items)


def calculate_total(items: list) -> float:
    """Return the subtotal plus tax."""
    subtotal = calculate_subtotal(items)
    return subtotal * (1 + TAX_RATE)
