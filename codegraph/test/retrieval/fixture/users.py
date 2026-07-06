"""User account fixture for hybrid search retrieval eval."""


def register_user(email, password):
    return verify_email(email)


def verify_email(email):
    return "@" in email


def deactivate_user(user_id):
    return {"user_id": user_id, "active": False}


def reset_password(user_id, new_password):
    return {"user_id": user_id, "reset": True}


def update_profile(user_id, fields):
    return {"user_id": user_id, **fields}
