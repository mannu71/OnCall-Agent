"""Shared AWS credential resolution.

Centralises the logic for obtaining AWS credentials from either an
explicit profile name or the DB-stored ``model_keys`` table.  Both
the CloudWatch node executor and the Bedrock LLM resolver can call
:func:`resolve_aws_credentials` instead of duplicating the look-up.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Provider aliases checked in priority order when looking up model keys.
_AWS_KEY_ALIASES = (
    "AWS CloudWatch",
    "cloudwatch",
    "AWS Bedrock",
    "bedrock",
    "aws bedrock",
    "aws",
)


async def resolve_aws_credentials(
    aws_profile: Optional[str] = None,
    aws_region: str = "us-east-1",
) -> Tuple[Dict[str, Any], str]:
    """Resolve AWS credentials from a profile or the DB model-key store.

    Priority:
    1. If *aws_profile* is supplied, return it as the sole credential entry.
    2. Otherwise iterate known provider aliases in the ``model_keys`` table
       and return the first match that has ``access_key_id``.

    Args:
        aws_profile: Optional AWS CLI profile name.
        aws_region: Default region (may be overridden by what is stored in DB).

    Returns:
        A ``(credentials_dict, resolved_region)`` tuple.  ``credentials_dict``
        will contain ``aws_profile`` **or** IAM key fields; it may be empty if
        no credentials were found (allowing the default credential chain to
        take over).
    """
    credentials: Dict[str, Any] = {}

    if aws_profile:
        credentials["aws_profile"] = aws_profile
        return credentials, aws_region

    try:
        from app.repositories import db_repository

        for key_name in _AWS_KEY_ALIASES:
            mk = await db_repository.get_model_key(key_name, include_secrets=True)
            if mk and mk.get("access_key_id"):
                credentials["access_key_id"] = mk["access_key_id"]
                if mk.get("secret_access_key"):
                    credentials["secret_access_key"] = mk["secret_access_key"]
                if mk.get("session_token"):
                    credentials["session_token"] = mk["session_token"]
                if mk.get("region") and (not aws_region or aws_region == "us-east-1"):
                    aws_region = mk["region"]
                break
    except Exception as exc:
        logger.warning("resolve_aws_credentials: could not load from model_keys: %s", exc)

    return credentials, aws_region
