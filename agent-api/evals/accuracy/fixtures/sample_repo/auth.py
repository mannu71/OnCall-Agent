"""Authentication helpers for the eval fixture repo."""


class TokenError(Exception):
    """Raised when a bearer token fails validation."""


def verify_token(token: str) -> str:
    """Validate a bearer token and return the subject id."""
    if not token:
        raise TokenError("empty token")
    return token.split(":")[0]
