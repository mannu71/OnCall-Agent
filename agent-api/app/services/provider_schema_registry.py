"""Provider Schema Registry for credential validation.

This module defines the schema models and registry for provider-specific
credential requirements. It provides a centralized way to define and access
credential schemas for different LLM providers.
"""

import logging
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CredentialFieldSchema(BaseModel):
    """Schema for a single credential field.
    
    Defines the structure and requirements for a credential field
    that a provider needs for authentication.
    """
    
    name: str = Field(..., description="Field name (e.g., 'api_key', 'access_key_id')")
    display_name: str = Field(..., description="Human-readable field name for UI display")
    description: str = Field(..., description="Description of what this field is used for")
    required: bool = Field(..., description="Whether this field is required")
    field_type: Literal["string", "secret"] = Field(..., description="Field type: 'string' for plain text, 'secret' for sensitive data")
    example: Optional[str] = Field(None, description="Example value (without actual secrets)")


class ProviderSchema(BaseModel):
    """Complete schema for a provider's credentials.
    
    Defines all credential requirements for a specific provider,
    including required and optional fields, authentication type,
    and documentation links.
    """
    
    provider: str = Field(..., description="Provider identifier (e.g., 'openai', 'anthropic')")
    display_name: str = Field(..., description="Human-readable provider name")
    description: str = Field(..., description="Description of the provider")
    auth_type: Literal["api_key", "aws_iam", "api_key_or_aws_iam"] = Field(
        ..., 
        description="Authentication type: 'api_key' for simple API keys, 'aws_iam' for AWS credentials, 'api_key_or_aws_iam' for providers supporting both"
    )
    documentation_url: str = Field(..., description="URL to provider's authentication documentation")
    fields: List[CredentialFieldSchema] = Field(..., description="List of credential fields for this provider")
    
    def get_required_fields(self) -> List[str]:
        """Returns list of required field names.
        
        Returns:
            List of field names that are marked as required.
        """
        return [f.name for f in self.fields if f.required]
    
    def get_optional_fields(self) -> List[str]:
        """Returns list of optional field names.
        
        Returns:
            List of field names that are marked as optional.
        """
        return [f.name for f in self.fields if not f.required]
    
    def get_all_field_names(self) -> List[str]:
        """Returns all valid field names for this provider.
        
        Returns:
            List of all field names (both required and optional).
        """
        return [f.name for f in self.fields]


# Provider schema definitions for all supported providers
PROVIDER_SCHEMAS = {
    "openai": ProviderSchema(
        provider="openai",
        display_name="OpenAI",
        description="OpenAI API for GPT models",
        auth_type="api_key",
        documentation_url="https://platform.openai.com/docs/api-reference/authentication",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="Your OpenAI API key from platform.openai.com",
                required=True,
                field_type="secret",
                example="sk-proj-..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Custom Endpoint",
                description="Optional custom API endpoint URL",
                required=False,
                field_type="string",
                example="https://api.openai.com/v1"
            )
        ]
    ),
    "anthropic": ProviderSchema(
        provider="anthropic",
        display_name="Anthropic",
        description="Anthropic API for Claude models",
        auth_type="api_key",
        documentation_url="https://docs.anthropic.com/en/api/getting-started",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="Your Anthropic API key",
                required=True,
                field_type="secret",
                example="sk-ant-..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Custom Endpoint",
                description="Optional custom API endpoint URL",
                required=False,
                field_type="string",
                example="https://api.anthropic.com"
            )
        ]
    ),
    "groq": ProviderSchema(
        provider="groq",
        display_name="Groq",
        description="Groq API for fast LLM inference",
        auth_type="api_key",
        documentation_url="https://console.groq.com/docs/quickstart",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="Your Groq API key",
                required=True,
                field_type="secret",
                example="gsk_..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Custom Endpoint",
                description="Optional custom API endpoint URL",
                required=False,
                field_type="string",
                example="https://api.groq.com/openai/v1"
            )
        ]
    ),
    "bedrock": ProviderSchema(
        provider="bedrock",
        display_name="AWS Bedrock",
        description="AWS Bedrock for foundation models",
        auth_type="api_key_or_aws_iam",
        documentation_url="https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="Bedrock API Key",
                description="AWS Bedrock API key (alternative to IAM credentials)",
                required=False,
                field_type="secret",
                example="bedrock_..."
            ),
            CredentialFieldSchema(
                name="access_key_id",
                display_name="AWS Access Key ID",
                description="AWS IAM access key ID (alternative to API key)",
                required=False,
                field_type="secret",
                example="AKIA..."
            ),
            CredentialFieldSchema(
                name="secret_access_key",
                display_name="AWS Secret Access Key",
                description="AWS IAM secret access key (required with access_key_id)",
                required=False,
                field_type="secret",
                example="wJalrXUtnFEMI/K7MDENG/..."
            ),
            CredentialFieldSchema(
                name="session_token",
                display_name="AWS Session Token",
                description="Optional AWS session token for temporary credentials",
                required=False,
                field_type="secret",
                example="FwoGZXIvYXdzE..."
            ),
            CredentialFieldSchema(
                name="region",
                display_name="AWS Region",
                description="AWS region for Bedrock service",
                required=True,
                field_type="string",
                example="us-east-1"
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Custom Endpoint",
                description="Optional custom Bedrock endpoint URL",
                required=False,
                field_type="string",
                example="https://bedrock-runtime.us-east-1.amazonaws.com"
            )
        ]
    ),
    "google": ProviderSchema(
        provider="google",
        display_name="Google AI",
        description="Google AI API for Gemini models",
        auth_type="api_key",
        documentation_url="https://ai.google.dev/gemini-api/docs/api-key",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="Your Google AI API key",
                required=True,
                field_type="secret",
                example="AIza..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Custom Endpoint",
                description="Optional custom API endpoint URL",
                required=False,
                field_type="string",
                example="https://generativelanguage.googleapis.com"
            )
        ]
    ),
    "azure openai": ProviderSchema(
        provider="azure openai",
        display_name="Azure OpenAI",
        description="Azure OpenAI Service for GPT models",
        auth_type="api_key",
        documentation_url="https://learn.microsoft.com/en-us/azure/ai-services/openai/quickstart",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="Your Azure OpenAI API key",
                required=True,
                field_type="secret",
                example="..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Endpoint URL",
                description="Your Azure OpenAI resource endpoint",
                required=True,
                field_type="string",
                example="https://your-resource.openai.azure.com"
            )
        ]
    ),
    "ollama": ProviderSchema(
        provider="ollama",
        display_name="Ollama",
        description="Ollama for local LLM inference",
        auth_type="api_key",
        documentation_url="https://github.com/ollama/ollama/blob/main/docs/api.md",
        fields=[
            CredentialFieldSchema(
                name="endpoint",
                display_name="Base URL",
                description="Ollama server endpoint URL",
                required=True,
                field_type="string",
                example="http://localhost:11434"
            )
        ]
    ),
    "custom": ProviderSchema(
        provider="custom",
        display_name="Custom",
        description="Custom OpenAI-compatible API endpoint",
        auth_type="api_key",
        documentation_url="https://platform.openai.com/docs/api-reference",
        fields=[
            CredentialFieldSchema(
                name="api_key",
                display_name="API Key",
                description="API key for authentication",
                required=False,
                field_type="secret",
                example="sk-..."
            ),
            CredentialFieldSchema(
                name="secret_key",
                display_name="Secret Key",
                description="Optional secret key for additional authentication",
                required=False,
                field_type="secret",
                example="..."
            ),
            CredentialFieldSchema(
                name="endpoint",
                display_name="Endpoint URL",
                description="Custom API endpoint URL",
                required=True,
                field_type="string",
                example="https://api.example.com/v1"
            )
        ]
    )
}


class ProviderSchemaRegistry:
    """Registry of provider credential schemas.
    
    Provides centralized access to credential schemas for all supported
    providers. Schemas define which fields are required, optional, and
    their types for each provider.
    """
    
    def __init__(self):
        """Initialize the registry and load provider schemas."""
        self._schemas: Dict[str, ProviderSchema] = {}
        self._load_schemas()
    
    def _load_schemas(self) -> None:
        """Load provider schemas from configuration.
        
        This method initializes the registry with schemas for all
        supported providers. Currently loads schemas from in-code
        definitions, but could be extended to load from external
        configuration files.
        """
        # Load schemas from PROVIDER_SCHEMAS constant
        self._schemas = PROVIDER_SCHEMAS.copy()
    
    def get_schema(self, provider: str) -> Optional[ProviderSchema]:
        """Get schema for a specific provider.
        
        Args:
            provider: Provider identifier (e.g., 'openai', 'anthropic')
            
        Returns:
            ProviderSchema if provider is supported, None otherwise.
        """
        schema = self._schemas.get(provider.lower())
        
        # Log schema retrieval request
        if schema:
            logger.info(
                f"Schema retrieved for provider '{provider}'",
                extra={
                    "provider": provider,
                    "auth_type": schema.auth_type,
                    "required_fields": schema.get_required_fields()
                }
            )
        else:
            logger.warning(
                f"Schema requested for unsupported provider '{provider}'",
                extra={
                    "provider": provider,
                    "supported_providers": list(self._schemas.keys())
                }
            )
        
        return schema
    
    def list_schemas(self) -> List[ProviderSchema]:
        """List all provider schemas.
        
        Returns:
            List of all registered provider schemas.
        """
        logger.info(
            f"Listing all provider schemas",
            extra={
                "schema_count": len(self._schemas),
                "providers": list(self._schemas.keys())
            }
        )
        return list(self._schemas.values())
    
    def get_supported_providers(self) -> List[str]:
        """Get list of supported provider names.
        
        Returns:
            List of provider identifiers that have registered schemas.
        """
        return list(self._schemas.keys())
    
    def is_provider_supported(self, provider: str) -> bool:
        """Check if a provider is supported.
        
        Args:
            provider: Provider identifier to check
            
        Returns:
            True if provider has a registered schema, False otherwise.
        """
        return provider.lower() in self._schemas
