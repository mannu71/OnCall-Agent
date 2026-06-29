"""Entry-point wiring for the eval fixture repo."""
from orders import OrderService


def handle_request(payload: dict) -> dict:
    """Top-level request handler: build the service and place the order."""
    service = OrderService()
    return service.place_order(payload["token"], payload["items"])
