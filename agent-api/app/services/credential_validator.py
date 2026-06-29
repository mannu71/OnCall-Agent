"""Credential Validator for provider-specific credential validation.

This module provides validation logic for provider credentials against
their defined schemas. It validates required fields, detects invalid fields,
and ensures appropriate authentication methods are provided.
"""

import logging
from typing import Dict, Any, List, Literal
from pydantic import BaseModel, Field

from app.services.provider_schema_registry import ProviderSchema, ProviderSchemaRegistry

logger = logging.getLogger(__name__)


class ValidationErrorDetail(BaseModel):
    """Detailed validation error information.
    
    Represents a single validation error with field name, error message,
    and error type classification.
    """
    
    field: str = Field(..., description="Name of the field that failed validation")
    message: str = Field(..., description="Human-readable error message")
    error_type: Literal["missing_required", "invalid_field", "invalid_value", "missing_auth_method"] = Field(
        ...,
        description="Type of validation error: 'missing_required' for missing required fields, "
                    "'invalid_field' for fields not in schema, 'invalid_value' for invalid field values, "
                    "'missing_auth_method' for missing authentication method"
    )


class CredentialValidationError(Exception):
    """Exception raised when credential validation fails.
    
    This exception stores validation errors along with provider and schema
    information, and provides methods to format human-readable messages
    and convert to API response format.
    """
    
    def __init__(
        self,
        provider: str,
        errors: List[ValidationErrorDetail],
        schema: ProviderSchema
    ):
        """Initialize validation error.
        
        Args:
            provider: Provider identifier (e.g., 'openai', 'anthropic')
            errors: List of validation error details
            schema: Provider schema that was used for validation
        """
        self.provider = provider
        self.errors = errors
        self.schema = schema
        super().__init__(self._format_message())
    
    def _format_message(self) -> str:
        """Format a human-readable error message.
        
        Creates a comprehensive error message that includes:
        - Provider name
        - List of all validation errors
        - Schema information for context
        
        Returns:
            Formatted error message string
        """
        error_lines = [f"Credential validation failed for provider '{self.provider}':"]
        
        for error in self.errors:
            error_lines.append(f"  - {error.field}: {error.message}")
        
        # Add schema reference
        error_lines.append(f"\nSee documentation: {self.schema.documentation_url}")
        
        return "\n".join(error_lines)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API response.
        
        Serializes the validation error into a dictionary format suitable
        for JSON API responses. Includes provider, errors, and schema info.
        
        Returns:
            Dictionary with error details:
            {
                "provider": str,
                "errors": [{"field": str, "message": str, "error_type": str}, ...],
                "schema": {...},
                "documentation_url": str
            }
        """
        return {
            "provider": self.provider,
            "errors": [error.model_dump() for error in self.errors],
            "schema": self.schema.model_dump(),
            "documentation_url": self.schema.documentation_url
        }


class CredentialValidator:
    """Validates credentials against provider schemas.
    
    This service validates credential data against provider-specific schemas
    from the ProviderSchemaRegistry. It checks for required fields, invalid
    fields, and appropriate authentication methods.
    """
    
    def __init__(self, registry: ProviderSchemaRegistry):
        """Initialize validator with schema registry.
        
        Args:
            registry: Provider schema registry for looking up schemas
        """
        self.registry = registry
    
    def validate(
        self,
        provider: str,
        credentials: Dict[str, Any]
    ) -> None:
        """Validate credentials against provider schema.
        
        Performs comprehensive validation including:
        - Provider support check
        - Required field validation
        - Invalid field detection
        - Authentication method validation
        
        Args:
            provider: Provider identifier (e.g., 'openai', 'anthropic')
            credentials: Dictionary of credential field names and values
            
        Raises:
            CredentialValidationError: If validation fails with detailed error information
        """
        # Get schema and validate provider is supported
        schema = self._validate_provider_supported(provider)
        
        # Collect all validation errors
        errors: List[ValidationErrorDetail] = []
        
        # Validate required fields are present
        errors.extend(self._validate_required_fields(schema, credentials))
        
        # Validate no extra/invalid fields are provided
        errors.extend(self._validate_no_extra_fields(schema, credentials))
        
        # Validate authentication method is appropriate
        errors.extend(self._validate_auth_method(schema, credentials))
        
        # If any errors found, raise exception
        if errors:
            # Log validation failure with provider and error details
            error_summary = ", ".join([f"{e.field}({e.error_type})" for e in errors])
            logger.warning(
                f"Credential validation failed for provider '{provider}': {error_summary}",
                extra={
                    "provider": provider,
                    "error_count": len(errors),
                    "error_types": [e.error_type for e in errors],
                    "failed_fields": [e.field for e in errors]
                }
            )
            raise CredentialValidationError(provider, errors, schema)
        
        # Log successful validation
        logger.info(
            f"Credential validation successful for provider '{provider}'",
            extra={
                "provider": provider,
                "configured_fields": list(credentials.keys())
            }
        )
    
    def _validate_provider_supported(self, provider: str) -> ProviderSchema:
        """Check if provider is supported.
        
        Args:
            provider: Provider identifier to check
            
        Returns:
            ProviderSchema for the provider
            
        Raises:
            CredentialValidationError: If provider is not supported
        """
        schema = self.registry.get_schema(provider)
        
        if schema is None:
            # Provider not supported
            supported = self.registry.get_supported_providers()
            error = ValidationErrorDetail(
                field="provider",
                message=f"Provider '{provider}' is not supported. Supported providers: {', '.join(supported)}",
                error_type="invalid_value"
            )
            # Create a minimal schema for error reporting
            from app.services.provider_schema_registry import ProviderSchema
            dummy_schema = ProviderSchema(
                provider=provider,
                display_name=provider,
                description="Unknown provider",
                auth_type="api_key",
                documentation_url="",
                fields=[]
            )
            raise CredentialValidationError(provider, [error], dummy_schema)
        
        return schema
    
    def _validate_required_fields(
        self,
        schema: ProviderSchema,
        credentials: Dict[str, Any]
    ) -> List[ValidationErrorDetail]:
        """Validate that all required fields are present.
        
        Args:
            schema: Provider schema defining required fields
            credentials: Credential data to validate
            
        Returns:
            List of validation errors for missing required fields
        """
        errors: List[ValidationErrorDetail] = []
        required_fields = schema.get_required_fields()
        
        for field_name in required_fields:
            # Check if field is present and not None/empty
            value = credentials.get(field_name)
            if value is None or (isinstance(value, str) and value.strip() == ""):
                # Find the field schema for better error message
                field_schema = next((f for f in schema.fields if f.name == field_name), None)
                if field_schema:
                    message = f"Required field '{field_schema.display_name}' is missing. {field_schema.description}"
                    if field_schema.example:
                        message += f" Example: {field_schema.example}"
                else:
                    message = f"Required field '{field_name}' is missing"
                
                errors.append(ValidationErrorDetail(
                    field=field_name,
                    message=message,
                    error_type="missing_required"
                ))
        
        return errors
    
    def _validate_no_extra_fields(
        self,
        schema: ProviderSchema,
        credentials: Dict[str, Any]
    ) -> List[ValidationErrorDetail]:
        """Validate that no non-applicable fields are provided.
        
        Args:
            schema: Provider schema defining valid fields
            credentials: Credential data to validate
            
        Returns:
            List of validation errors for invalid fields
        """
        errors: List[ValidationErrorDetail] = []
        valid_fields = schema.get_all_field_names()
        
        # Also allow 'description' field which is not in schema but is valid
        valid_fields.append("description")
        
        for field_name in credentials.keys():
            if field_name not in valid_fields:
                errors.append(ValidationErrorDetail(
                    field=field_name,
                    message=f"Field '{field_name}' is not valid for provider '{schema.provider}'. "
                           f"Valid fields: {', '.join(valid_fields)}",
                    error_type="invalid_field"
                ))
        
        return errors
    
    def _validate_auth_method(
        self,
        schema: ProviderSchema,
        credentials: Dict[str, Any]
    ) -> List[ValidationErrorDetail]:
        """Validate that appropriate authentication method is provided.
        
        For providers with multiple auth methods (e.g., Bedrock with api_key_or_aws_iam),
        validates that at least one complete authentication method is provided.
        
        Args:
            schema: Provider schema defining authentication requirements
            credentials: Credential data to validate
            
        Returns:
            List of validation errors for missing authentication methods
        """
        errors: List[ValidationErrorDetail] = []
        
        # Only validate for providers that support multiple auth methods
        if schema.auth_type == "api_key_or_aws_iam":
            # Check if either api_key OR (access_key_id + secret_access_key) is provided
            has_api_key = credentials.get("api_key") and credentials.get("api_key").strip()
            has_iam_creds = (
                credentials.get("access_key_id") and credentials.get("access_key_id").strip() and
                credentials.get("secret_access_key") and credentials.get("secret_access_key").strip()
            )
            
            if not has_api_key and not has_iam_creds:
                errors.append(ValidationErrorDetail(
                    field="authentication",
                    message=f"Provider '{schema.provider}' requires either 'api_key' OR "
                           f"('access_key_id' + 'secret_access_key'). Please provide one authentication method.",
                    error_type="missing_auth_method"
                ))
        
        return errors
    
    def _normalize_field_names(
        self,
        credentials: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Normalize aws_* prefixed field names to non-prefixed versions.
        
        Provides backward compatibility by converting aws_access_key_id to access_key_id, etc.
        
        Args:
            credentials: Credential data with potentially prefixed field names
            
        Returns:
            Normalized credential dictionary with non-prefixed field names
        """
        normalized = credentials.copy()
        
        # Mapping of prefixed names to normalized names
        field_mapping = {
            "aws_access_key_id": "access_key_id",
            "aws_secret_access_key": "secret_access_key",
            "aws_session_token": "session_token"
        }
        
        for prefixed_name, normalized_name in field_mapping.items():
            if prefixed_name in normalized:
                # Prefer the prefixed version if both exist
                normalized[normalized_name] = normalized.pop(prefixed_name)
        
        return normalized
