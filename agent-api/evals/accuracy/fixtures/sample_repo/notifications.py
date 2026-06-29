"""Outbound notifications for the eval fixture repo."""


def send_receipt(subject_id: str, total: float) -> bool:
    """Pretend to email a receipt to the subject."""
    return bool(subject_id) and total >= 0
