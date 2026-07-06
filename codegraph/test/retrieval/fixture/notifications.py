"""Notification fixture for hybrid search retrieval eval."""


def send_email(user_id, subject, body):
    return queue_notification("email", user_id, body)


def send_sms(user_id, text):
    return queue_notification("sms", user_id, text)


def queue_notification(channel, user_id, body):
    return {"channel": channel, "user_id": user_id, "body": body}


def retry_notification(notification_id):
    return {"notification_id": notification_id, "retried": True}
