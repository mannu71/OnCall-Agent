"""Credential Transformer for field name normalization.

This module provides transformation logic for credential field names to support
backward compatibility with aws_* prefixed field names. It handles conversion
between API format and database format, and provides secret masking capabilities.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class CredentialTransformer:
    """Transforms credential data for backward compatibility.
    
    This service handles field name normalization to support both aws_* prefixed
    and non-prefixed field names. It provides methods to:
    - Normalize input data (convert aws_* to non-prefixed, with aws_* taking precedence)
    - Convert between API and database formats
    - Mask secret values in responses
    """
    
    # Field name mapping: API field name -> Database field name
    # Includes both aws_* prefixed and non-prefixed versions for backward compatibility
    FIELD_MAPPING = {
        "api_key": "api_key",
        "secret_key": "secret_key",
        "endpoint": "endpoint",
        "region": "region",
        "access_key_id": "access_key_id",
        "aws_access_key_id": "access_key_id",  # Backward compat
        "secret_access_key": "secret_access_key",
        "aws_secret_access_key": "secret_access_key",  # Backward compat
        "session_token": "session_token",
        "aws_session_token": "session_token",  # Backward compat
        "description": "description",
        "provider": "provider"
    }
    
    # Fields that contain sensitive data and should be masked
    SECRET_FIELDS = {
        "api_key",
        "secret_key",
        "access_key_id",
        "secret_access_key",
        "session_token"
    }
    
    @classmethod
    def normalize_input(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize input data by converting aws_* prefixed fields.
        
        Converts aws_* prefixed field names to their non-prefixed equivalents.
        If both prefixed and non-prefixed versions are provided, the aws_* 
        prefixed version takes precedence.
        
        Examples:
            >>> CredentialTransformer.normalize_input({
            ...     "aws_access_key_id": "AKIA123",
            ...     "access_key_id": "AKIA456"
            ... })
            {"access_key_id": "AKIA123"}  # aws_* version takes precedence
            
            >>> CredentialTransformer.normalize_input({
            ...     "access_key_id": "AKIA456"
            ... })
            {"access_key_id": "AKIA456"}  # non-prefixed version used
        
        Args:
            data: Input credential data with potentially prefixed field names
            
        Returns:
            Normalized dictionary with non-prefixed field names
        """
        normalized = {}
        
        # Track which non-prefixed fields we've seen from aws_* versions
        # This ensures aws_* takes precedence
        prefixed_fields_seen = set()
        
        # Track aws_* prefixed fields for logging
        aws_prefixed_fields_used = []
        
        # First pass: process aws_* prefixed fields (these take precedence)
        for key, value in data.items():
            if key.startswith("aws_") and key in cls.FIELD_MAPPING:
                # Map to non-prefixed version
                normalized_key = cls.FIELD_MAPPING[key]
                normalized[normalized_key] = value
                prefixed_fields_seen.add(normalized_key)
                aws_prefixed_fields_used.append(key)
        
        # Second pass: process non-prefixed fields (only if not already set by aws_* version)
        for key, value in data.items():
            if key in cls.FIELD_MAPPING:
                normalized_key = cls.FIELD_MAPPING[key]
                # Only add if we haven't already added it from an aws_* version
                if normalized_key not in prefixed_fields_seen:
                    normalized[normalized_key] = value
        
        # Log usage of aws_* prefixed fields (backward compatibility tracking)
        if aws_prefixed_fields_used:
            logger.info(
                f"Legacy aws_* prefixed field names used: {', '.join(aws_prefixed_fields_used)}",
                extra={
                    "aws_prefixed_fields": aws_prefixed_fields_used,
                    "normalized_fields": list(prefixed_fields_seen),
                    "backward_compatibility": True
                }
            )
        
        return normalized
    
    @classmethod
    def to_database_format(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert API format to database format.
        
        Transforms credential data from API format to the format expected
        by the database. This includes normalizing field names and ensuring
        only valid database fields are included.
        
        Args:
            data: Credential data in API format
            
        Returns:
            Dictionary in database format with normalized field names
        """
        # First normalize the input to handle aws_* prefixes
        normalized = cls.normalize_input(data)
        
        # Filter to only include fields that map to database columns
        db_format = {}
        
        for key, value in normalized.items():
            if key in cls.FIELD_MAPPING.values():
                db_format[key] = value
        
        return db_format
    
    @classmethod
    def from_database_format(
        cls,
        data: Dict[str, Any],
        include_secrets: bool = False
    ) -> Dict[str, Any]:
        """Convert database format to API format.
        
        Transforms credential data from database format to API response format.
        When include_secrets is False, secret fields are masked with asterisks.
        
        Examples:
            >>> CredentialTransformer.from_database_format({
            ...     "api_key": "sk-proj-abc123",
            ...     "provider": "openai"
            ... }, include_secrets=False)
            {"api_key": "***", "provider": "openai"}
            
            >>> CredentialTransformer.from_database_format({
            ...     "api_key": "sk-proj-abc123",
            ...     "provider": "openai"
            ... }, include_secrets=True)
            {"api_key": "sk-proj-abc123", "provider": "openai"}
        
        Args:
            data: Credential data in database format
            include_secrets: If False, mask secret values with "***"
            
        Returns:
            Dictionary in API format with secrets masked if requested
        """
        api_format = {}
        
        for key, value in data.items():
            if value is None:
                # Skip None values
                continue
            
            # Check if this is a secret field and should be masked
            if not include_secrets and key in cls.SECRET_FIELDS:
                # Mask the secret value
                api_format[key] = "***"
            else:
                # Include the actual value
                api_format[key] = value
        
        return api_format
