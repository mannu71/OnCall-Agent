"""Order lifecycle fixture for hybrid search retrieval eval."""


def create_order(user_id, items):
    total = calculate_total(items)
    return {"user_id": user_id, "total": total}


def calculate_total(items):
    return sum(item["price"] for item in items)


def cancel_order(order_id):
    return {"order_id": order_id, "cancelled": True}


def ship_order(order_id, address):
    return {"order_id": order_id, "address": address, "shipped": True}


def track_order(order_id):
    return {"order_id": order_id, "status": "in_transit"}
