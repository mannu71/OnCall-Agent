"""Shared AWS credential resolution.

Centralises the logic for obtaining AWS credentials from either an
explicit profile name or the DB-stored ``model_keys`` table.  Both
the CloudWatch node executor and the Bedrock LLM resolver can call
:func:`resolve_aws_credentials` instead of duplicating the look-up.
"""
from __future__ import annotations

import configparser
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
        from app.infrastructure.persistence import model_key_repository

        for key_name in _AWS_KEY_ALIASES:
            mk = await model_key_repository.get_by_provider(key_name, include_secrets=True)
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


def _aws_config_paths() -> Tuple[str, str]:
    """Return the (credentials, config) file paths honouring AWS env overrides."""
    cred_path = os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or os.path.expanduser(
        os.path.join("~", ".aws", "credentials")
    )
    config_path = os.environ.get("AWS_CONFIG_FILE") or os.path.expanduser(
        os.path.join("~", ".aws", "config")
    )
    return cred_path, config_path


def _parse_expiration(raw: Optional[str]) -> Tuple[Optional[str], Optional[bool]]:
    """Parse an ``aws_expiration`` timestamp into ``(iso, expired)``.

    Returns ``(None, None)`` when no expiration is recorded (e.g. long-lived IAM
    keys) — "expired" is genuinely unknown in that case, never assumed False.
    """
    if not raw:
        return None, None
    val = raw.strip()
    try:
        # Normalise trailing Z to an explicit UTC offset for fromisoformat.
        iso = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(), dt <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return val, None


def list_aws_profiles() -> List[Dict[str, Any]]:
    """Enumerate AWS profiles from the shared credentials/config files.

    Reads ``~/.aws/credentials`` (sections are bare profile names) and
    ``~/.aws/config`` (sections are ``[profile <name>]``, or ``[default]``),
    honouring ``AWS_SHARED_CREDENTIALS_FILE`` / ``AWS_CONFIG_FILE``. For each
    profile it reports the region and the temporary-credential expiry state
    (parsed from the non-standard ``aws_expiration`` key written by SSO/STS
    refresh flows). No network calls are made — this is a cheap, offline probe
    so the node-config dropdown can show valid-vs-expired at a glance.

    Returns a list of ``{name, region, has_credentials, expiration, expired}``
    dicts sorted valid-first, then by name. ``expired`` is ``None`` (unknown)
    for long-lived keys with no recorded expiry.
    """
    cred_path, config_path = _aws_config_paths()
    profiles: Dict[str, Dict[str, Any]] = {}

    def _ensure(name: str) -> Dict[str, Any]:
        return profiles.setdefault(
            name,
            {
                "name": name,
                "region": None,
                "has_credentials": False,
                "expiration": None,
                "expired": None,
            },
        )

    # 1) credentials file — sections are bare profile names, carry keys/expiry.
    if os.path.isfile(cred_path):
        try:
            cp = configparser.RawConfigParser()
            cp.read(cred_path)
            for section in cp.sections():
                entry = _ensure(section)
                if cp.has_option(section, "aws_access_key_id"):
                    entry["has_credentials"] = True
                if cp.has_option(section, "region"):
                    entry["region"] = cp.get(section, "region")
                if cp.has_option(section, "aws_expiration"):
                    iso, expired = _parse_expiration(cp.get(section, "aws_expiration"))
                    entry["expiration"], entry["expired"] = iso, expired
        except configparser.Error as exc:
            logger.warning("list_aws_profiles: could not parse credentials file: %s", exc)

    # 2) config file — sections are "[profile name]" (or bare "[default]"); may
    # supply a region for profiles whose creds live in the credentials file.
    if os.path.isfile(config_path):
        try:
            cp = configparser.RawConfigParser()
            cp.read(config_path)
            for section in cp.sections():
                name = section[len("profile "):] if section.startswith("profile ") else section
                entry = _ensure(name)
                if not entry["region"] and cp.has_option(section, "region"):
                    entry["region"] = cp.get(section, "region")
        except configparser.Error as exc:
            logger.warning("list_aws_profiles: could not parse config file: %s", exc)

    # Sort: valid first (expired is False), then unknown, then expired; name tiebreak.
    def _rank(p: Dict[str, Any]) -> Tuple[int, str]:
        expired = p.get("expired")
        bucket = 0 if expired is False else (1 if expired is None else 2)
        return bucket, p.get("name", "")

    return sorted(profiles.values(), key=_rank)
