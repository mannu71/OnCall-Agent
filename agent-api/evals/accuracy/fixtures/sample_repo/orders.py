"""Order orchestration for the eval fixture repo."""
from auth import verify_token
from pricing import calculate_total
from notifications import send_receipt


class OrderService:
    """Coordinates validating, pricing, and confirming an order."""

    def place_order(self, token: str, items: list) -> dict:
        """Validate the token, price the cart, and send a receipt."""
        subject = verify_token(token)
        total = calculate_total(items)
        send_receipt(subject, total)
        return {"subject": subject, "total": total}
