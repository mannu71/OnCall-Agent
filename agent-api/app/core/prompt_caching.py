"""
Anthropic Prompt Caching Support

This module implements Anthropic's prompt caching optimization to reduce token costs
on multi-turn conversations by marking specific messages for caching.

Strategy: system_and_3
- Cache the system prompt
- Cache the last 3 non-system messages
- Maximum of 4 cache breakpoints total

References:
- https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
"""

import copy
from typing import Any, Dict, List, Literal

# Valid TTL values for Anthropic prompt caching
CacheTTL = Literal["5m", "1h"]


def apply_anthropic_cache_control(
    messages: List[Dict[str, Any]],
    cache_ttl: CacheTTL = "5m",
    native_anthropic: bool = False,
) -> List[Dict[str, Any]]:
    """
    Apply system_and_3 caching strategy to messages.
    
    Places up to 4 cache_control breakpoints:
    1. System prompt (if present)
    2-4. Last 3 non-system messages
    
    The cache_control marker is placed on the last content block of each marked message.
    
    Args:
        messages: Original message list (will not be modified)
        cache_ttl: Time-to-live for cached content ("5m" or "1h")
        native_anthropic: Whether using native Anthropic SDK format
    
    Returns:
        Deep copy of messages with cache_control markers added
        
    Examples:
        >>> messages = [
        ...     {"role": "system", "content": "You are a helpful assistant"},
        ...     {"role": "user", "content": "Hello"},
        ...     {"role": "assistant", "content": "Hi there!"},
        ...     {"role": "user", "content": "How are you?"},
        ... ]
        >>> cached = apply_anthropic_cache_control(messages)
        >>> # System message and last 3 messages will have cache_control markers
    """
    if not messages:
        return []
    
    # Deep copy to avoid modifying original
    messages_copy = copy.deepcopy(messages)
    
    # Build cache control object based on TTL
    cache_control = _build_cache_control(cache_ttl, native_anthropic)
    
    # Track which messages to mark (up to 4 total)
    messages_to_mark = []
    
    # 1. Find system message (if present)
    system_idx = None
    for idx, msg in enumerate(messages_copy):
        if msg.get("role") == "system":
            system_idx = idx
            messages_to_mark.append(idx)
            break
    
    # 2. Find last 3 non-system messages
    non_system_indices = [
        idx for idx, msg in enumerate(messages_copy)
        if msg.get("role") != "system"
    ]
    
    # Take last 3 non-system messages
    last_three = non_system_indices[-3:] if len(non_system_indices) >= 3 else non_system_indices
    messages_to_mark.extend(last_three)
    
    # Ensure we don't exceed 4 markers
    messages_to_mark = messages_to_mark[:4]
    
    # Apply cache_control to the last content block of each marked message
    for idx in messages_to_mark:
        _apply_cache_control_to_message(messages_copy[idx], cache_control)
    
    return messages_copy


def _build_cache_control(cache_ttl: CacheTTL, native_anthropic: bool) -> Dict[str, Any]:
    """
    Build cache_control object based on TTL and SDK format.
    
    Args:
        cache_ttl: Time-to-live ("5m" or "1h")
        native_anthropic: Whether using native Anthropic SDK
        
    Returns:
        Cache control dictionary
    """
    # Convert TTL to seconds for native SDK
    ttl_seconds = {
        "5m": 300,
        "1h": 3600,
    }
    
    if native_anthropic:
        # Native Anthropic SDK format
        return {
            "type": "ephemeral",
            "ttl_seconds": ttl_seconds.get(cache_ttl, 300),
        }
    else:
        # OpenAI-compatible format (used by most providers)
        return {
            "type": "ephemeral",
        }


def _apply_cache_control_to_message(message: Dict[str, Any], cache_control: Dict[str, Any]) -> None:
    """
    Apply cache_control to the last content block of a message.
    
    Modifies the message in-place.
    
    Args:
        message: Message dictionary to modify
        cache_control: Cache control object to apply
    """
    content = message.get("content")
    
    if not content:
        return
    
    # Handle string content
    if isinstance(content, str):
        # Convert to content block format
        message["content"] = [
            {
                "type": "text",
                "text": content,
                "cache_control": cache_control,
            }
        ]
        return
    
    # Handle list of content blocks
    if isinstance(content, list) and len(content) > 0:
        last_block = content[-1]
        
        # Add cache_control to last block
        if isinstance(last_block, dict):
            last_block["cache_control"] = cache_control
        elif isinstance(last_block, str):
            # Convert string block to dict
            content[-1] = {
                "type": "text",
                "text": last_block,
                "cache_control": cache_control,
            }


def count_cache_markers(messages: List[Dict[str, Any]]) -> int:
    """
    Count the number of cache_control markers in a message list.
    
    Useful for testing and validation.
    
    Args:
        messages: Message list to inspect
        
    Returns:
        Number of messages with cache_control markers
    """
    count = 0
    
    for msg in messages:
        content = msg.get("content")
        
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    count += 1
                    break  # Only count once per message
        elif isinstance(content, dict) and "cache_control" in content:
            count += 1
    
    return count


def has_cache_markers(messages: List[Dict[str, Any]]) -> bool:
    """
    Check if any messages have cache_control markers.
    
    Args:
        messages: Message list to inspect
        
    Returns:
        True if at least one message has cache_control markers
    """
    return count_cache_markers(messages) > 0


def remove_cache_markers(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove all cache_control markers from messages.
    
    Useful for testing or when switching providers.
    
    Args:
        messages: Message list (will not be modified)
        
    Returns:
        Deep copy of messages with cache_control markers removed
    """
    messages_copy = copy.deepcopy(messages)
    
    for msg in messages_copy:
        content = msg.get("content")
        
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "cache_control" in block:
                    del block["cache_control"]
    
    return messages_copy
