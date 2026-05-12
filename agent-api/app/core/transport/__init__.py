"""ProviderTransport — unified LLM provider abstraction.

Usage:
    from app.core.transport import get_transport
    transport = get_transport()            # uses PROVIDER_TRANSPORT env var
    response = await transport.complete(messages, model="claude-sonnet-4-6")
"""
from app.core.transport.provider import ProviderTransport, TransportMessage, TransportResponse
from app.core.transport.factory import get_transport

__all__ = ["ProviderTransport", "TransportMessage", "TransportResponse", "get_transport"]
