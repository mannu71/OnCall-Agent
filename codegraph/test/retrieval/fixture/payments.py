"""Payment fixture — deliberately many functions in one file to exercise the
per-file diversity cap in hybrid search_graph results."""


def process_payment(order_id, amount):
    card = validate_card(order_id)
    return charge_card(card, amount)


def validate_card(order_id):
    return {"order_id": order_id, "valid": True}


def charge_card(card, amount):
    return authorize_payment(card, amount)


def authorize_payment(card, amount):
    return capture_payment(card, amount)


def capture_payment(card, amount):
    return settle_batch([card])


def settle_batch(cards):
    return len(cards)


def refund_payment(order_id, amount):
    return void_transaction(order_id, amount)


def void_transaction(order_id, amount):
    return {"order_id": order_id, "amount": amount, "voided": True}


def retry_payment(order_id, amount):
    return process_payment(order_id, amount)


def cancel_subscription(subscription_id):
    return void_transaction(subscription_id, 0)
