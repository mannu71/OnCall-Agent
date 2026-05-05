"""Context compression for managing long conversations.

Reduces conversation token footprint through intelligent summarization
while preserving critical information.

Key features:
- Token-budget based tail protection (not fixed message count)
- Head protection for system prompt + initial exchange
- Tool-call/result pair integrity preservation
- Structured LLM-based summarization
- Iterative summary updates

Requirements: 1.1-1.9
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.model_metadata import (
    DEFAULT_FALLBACK_CONTEXT,
    MINIMUM_CONTEXT_LENGTH,
    estimate_messages_tokens_rough,
    get_model_context_length_sync,
)



logger = logging.getLogger(__name__)


# Template for structured LLM summarization
# Requirements: 1.4, 1.6
SUMMARY_TEMPLATE = """## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Progress
### Done
[Completed work — include specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why they were made]

## Resolved Questions
[Questions the user asked that were ALREADY answered]

## Pending User Asks
[Questions or requests NOT yet answered or fulfilled]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Remaining Work
[What remains to be done — framed as context, not instructions]

## Critical Context
[Any specific values, error messages, configuration details]

## Tools & Patterns
[Which tools were used, how they were used effectively]
"""

# System prompt for generating structured summaries
SUMMARY_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to create a structured summary of a conversation that preserves all critical information for future context.

Generate a summary following the template structure exactly. Be concise but comprehensive. Focus on:
1. What the user is trying to accomplish (Goal)
2. Any constraints, preferences, or important decisions
3. What has been done, what's in progress, and what's blocked
4. Key technical decisions and their rationale
5. Questions that were answered and questions still pending
6. Files that were touched and their relevance
7. What work remains
8. Any critical values, errors, or configuration details
9. Tools and patterns that were effective

Do not include instructions or next steps - only summarize what has happened and what is known."""

# Default token budget for tail protection (as fraction of context length)
DEFAULT_TAIL_BUDGET_PERCENT = 0.25  # 25% of context length

# Minimum messages to keep in tail regardless of token budget
MIN_TAIL_MESSAGES = 2


@dataclass
class CompressionResult:
    """Result of a compression operation."""
    
    messages: List[Dict[str, Any]]
    original_token_count: int
    compressed_token_count: int
    head_count: int
    tail_count: int
    summary_count: int
    was_compressed: bool
    summary: Optional[str] = None


class ContextCompressor:
    """Compresses conversation context via lossy summarization.
    
    The compressor protects:
    1. Head messages (system prompt + first N messages)
    2. Tail messages (based on token budget, not fixed count)
    
    The middle section can be summarized or pruned.
    
    Attributes:
        model: The model identifier for context length resolution
        threshold_percent: Fraction of context length to trigger compression
        protect_first_n: Number of initial messages to always protect
        context_length: Resolved context length for the model
        threshold_tokens: Token count threshold for compression
    """
    
    def __init__(
        self,
        model: str,
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        tail_budget_percent: float = DEFAULT_TAIL_BUDGET_PERCENT,
    ):
        """Initialize the context compressor.
        
        Args:
            model: Model identifier for context length resolution
            threshold_percent: Fraction of context length to trigger compression (default 0.50)
            protect_first_n: Number of initial messages to protect (default 3)
            base_url: Optional base URL for the model provider
            api_key: Optional API key for the model provider
            provider: Optional provider name
            tail_budget_percent: Fraction of context length for tail budget (default 0.25)
        """
        self.model = model
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.tail_budget_percent = tail_budget_percent
        self.base_url = base_url
        self.api_key = api_key
        self.provider = provider
        
        # Resolve context length
        # Note: provider is stored for future use but not used in sync resolution
        self.context_length = get_model_context_length_sync(
            model=model,
            base_url=base_url,
        )
        
        # Calculate threshold tokens
        self.threshold_tokens = max(
            int(self.context_length * threshold_percent),
            MINIMUM_CONTEXT_LENGTH,
        )
        
        # Calculate tail token budget
        self.tail_token_budget = max(
            int(self.context_length * tail_budget_percent),
            8_000,  # Minimum 8K tokens for tail
        )
        
        # Track previous summary for iterative updates
        self._previous_summary: Optional[str] = None
        
        logger.debug(
            f"ContextCompressor initialized: model={model}, "
            f"context_length={self.context_length}, "
            f"threshold_tokens={self.threshold_tokens}, "
            f"protect_first_n={protect_first_n}, "
            f"tail_token_budget={self.tail_token_budget}"
        )
    
    def should_compress(self, prompt_tokens: int) -> bool:
        """Check if context exceeds compression threshold.
        
        Args:
            prompt_tokens: Current token count of the prompt/messages
        
        Returns:
            True if compression should be triggered, False otherwise
        
        Validated by: Property 1 - Compression Threshold Trigger
        """
        should = prompt_tokens > self.threshold_tokens
        if should:
            logger.info(
                f"Compression threshold exceeded: {prompt_tokens} > {self.threshold_tokens} "
                f"({self.threshold_percent * 100:.0f}% of {self.context_length})"
            )
        return should
    
    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compress messages by protecting head and tail, summarizing middle.
        
        This is a synchronous wrapper that performs basic compression.
        For LLM-based summarization, use compress_async instead.
        
        Algorithm:
        1. Prune large tool outputs (cheap pre-pass)
        2. Protect head messages (system + first exchange)
        3. Protect tail by token budget (not fixed message count)
        4. Identify tool-call/result pairs that must stay together
        5. Summarize or prune the middle section
        
        Args:
            messages: List of conversation messages
            current_tokens: Optional pre-calculated token count
            focus_topic: Optional topic to prioritize in summarization
        
        Returns:
            Compressed message list
        
        Validated by:
            Property 2 - Head Message Protection
            Property 3 - Token-Budget Tail Protection
            Property 4 - Tool-Call/Result Pair Integrity
        """
        if not messages:
            return messages
        
        # Calculate tokens if not provided
        if current_tokens is None:
            current_tokens = estimate_messages_tokens_rough(messages)
        
        # Check if compression is needed
        if not self.should_compress(current_tokens):
            logger.debug("Compression not needed")
            return messages
        
        logger.info(
            f"Starting compression: {len(messages)} messages, {current_tokens} tokens"
        )
        
        # Step 1: Prune large tool outputs (cheap pre-pass)
        messages = self._prune_old_tool_results(messages)
        
        # Recalculate tokens after pruning
        current_tokens = estimate_messages_tokens_rough(messages)
        
        # Step 2: Protect head messages
        head_messages, remaining_messages = self._protect_head(messages)
        
        # Step 3: Protect tail by token budget
        tail_messages, middle_messages = self._protect_tail_by_budget(remaining_messages)
        
        # Step 4: Preserve tool-call/result pair integrity
        middle_messages, additional_tail = self._preserve_tool_pairs(
            middle_messages, tail_messages
        )
        tail_messages = additional_tail + tail_messages
        
        # Step 5: Handle middle section (basic truncation for sync version)
        if middle_messages:
            logger.debug(
                f"Truncating {len(middle_messages)} middle messages "
                f"(use compress_async for LLM summarization)"
            )
            # Create a placeholder summary message
            summary_msg = self._create_truncation_summary(middle_messages)
            middle_messages = [summary_msg] if summary_msg else []
        
        # Combine all sections
        compressed = head_messages + middle_messages + tail_messages
        
        compressed_tokens = estimate_messages_tokens_rough(compressed)
        logger.info(
            f"Compression complete: {len(messages)} -> {len(compressed)} messages, "
            f"{current_tokens} -> {compressed_tokens} tokens"
        )
        
        return compressed
    
    async def compress_async(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compress messages with LLM-based structured summarization.
        
        This is the async version that uses LLM for intelligent summarization.
        Falls back gracefully to basic truncation on any summarization failure.
        
        Algorithm:
        1. Prune large tool outputs (cheap pre-pass)
        2. Protect head messages (system + first exchange)
        3. Protect tail by token budget (not fixed message count)
        4. Identify tool-call/result pairs that must stay together
        5. Generate structured LLM summary of middle section
        6. Update previous summary iteratively
        7. Fallback to basic truncation on any failure
        
        Args:
            messages: List of conversation messages
            current_tokens: Optional pre-calculated token count
            focus_topic: Optional topic to prioritize in summarization.
                         When provided, the summarization will prioritize
                         preserving information related to this topic.
        
        Returns:
            Compressed message list
        
        Requirements: 1.4, 1.6, 1.8, 1.9
        
        Validated by:
            Property 2 - Head Message Protection
            Property 3 - Token-Budget Tail Protection
            Property 4 - Tool-Call/Result Pair Integrity
        """
        if not messages:
            return messages
        
        # Calculate tokens if not provided
        if current_tokens is None:
            current_tokens = estimate_messages_tokens_rough(messages)
        
        # Check if compression is needed
        if not self.should_compress(current_tokens):
            logger.debug("Compression not needed")
            return messages
        
        logger.info(
            f"Starting async compression: {len(messages)} messages, {current_tokens} tokens"
            + (f", focus_topic='{focus_topic}'" if focus_topic else "")
        )
        
        # Step 1: Prune large tool outputs (cheap pre-pass)
        messages = self._prune_old_tool_results(messages)
        
        # Recalculate tokens after pruning
        current_tokens = estimate_messages_tokens_rough(messages)
        
        # Step 2: Protect head messages
        head_messages, remaining_messages = self._protect_head(messages)
        
        # Step 3: Protect tail by token budget
        tail_messages, middle_messages = self._protect_tail_by_budget(remaining_messages)
        
        # Step 4: Preserve tool-call/result pair integrity
        middle_messages, additional_tail = self._preserve_tool_pairs(
            middle_messages, tail_messages
        )
        tail_messages = additional_tail + tail_messages
        
        # Step 5: Generate structured LLM summary of middle section
        # Requirements: 1.8 (graceful fallback), 1.9 (focus-topic support)
        if middle_messages:
            logger.info(f"Generating structured summary for {len(middle_messages)} middle messages")
            
            summary_msg = await self._compress_middle_with_fallback(
                middle_messages, focus_topic
            )
            middle_messages = [summary_msg] if summary_msg else []
        
        # Combine all sections
        compressed = head_messages + middle_messages + tail_messages
        
        compressed_tokens = estimate_messages_tokens_rough(compressed)
        logger.info(
            f"Async compression complete: {len(messages)} -> {len(compressed)} messages, "
            f"{current_tokens} -> {compressed_tokens} tokens"
        )
        
        return compressed
    
    async def _compress_middle_with_fallback(
        self,
        middle_messages: List[Dict[str, Any]],
        focus_topic: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Compress middle section with graceful fallback on failure.
        
        This method attempts LLM-based structured summarization first,
        then falls back to basic truncation if that fails. It never
        blocks execution - on any failure, it returns a basic summary.
        
        Args:
            middle_messages: Messages to compress
            focus_topic: Optional topic to prioritize in summarization
        
        Returns:
            Summary message or None
        
        Requirements: 1.8, 1.9
        """
        if not middle_messages:
            return None
        
        # Try LLM-based structured summarization
        try:
            summary_text = await self._generate_structured_summary(
                middle_messages, focus_topic
            )
            
            if summary_text:
                # Include focus topic in summary header if provided
                header = "[Context Summary]"
                if focus_topic:
                    header += f" (Focus: {focus_topic})"
                
                return {
                    "role": "assistant",
                    "content": f"{header}\n\n{summary_text}",
                }
            
            # LLM returned None, fall back to basic
            logger.warning("LLM summarization returned None, falling back to basic truncation")
            return self._create_truncation_summary(middle_messages, focus_topic)
            
        except Exception as e:
            # Requirement 1.8: Graceful fallback without blocking execution
            logger.warning(
                f"Structured summarization failed, falling back to basic truncation: {e}",
                exc_info=logger.isEnabledFor(logging.DEBUG)
            )
            return self._create_truncation_summary(middle_messages, focus_topic)
    
    def _protect_head(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Protect the first N messages from compression.
        
        Args:
            messages: Full message list
        
        Returns:
            Tuple of (head_messages, remaining_messages)
        
        Validated by: Property 2 - Head Message Protection
        """
        if len(messages) <= self.protect_first_n:
            # All messages are protected
            return messages, []
        
        head = messages[:self.protect_first_n]
        remaining = messages[self.protect_first_n:]
        
        logger.debug(f"Protected {len(head)} head messages")
        return head, remaining
    
    def _protect_tail_by_budget(
        self, messages: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Protect tail messages based on token budget, not fixed count.
        
        This ensures the most recent context is preserved with a token
        budget rather than a fixed number of messages, which is more
        accurate for varying message sizes.
        
        Args:
            messages: Messages to split (after head protection)
        
        Returns:
            Tuple of (middle_messages, tail_messages)
        
        Validated by: Property 3 - Token-Budget Tail Protection
        """
        if not messages:
            return [], []
        
        tail_messages: List[Dict[str, Any]] = []
        tail_tokens = 0
        
        # Iterate from end to start, accumulating messages until budget exhausted
        for msg in reversed(messages):
            msg_tokens = estimate_messages_tokens_rough([msg])
            
            if tail_tokens + msg_tokens <= self.tail_token_budget:
                tail_messages.insert(0, msg)  # Insert at front to maintain order
                tail_tokens += msg_tokens
            else:
                # Budget exhausted
                break
        
        # Ensure minimum tail messages
        if len(tail_messages) < MIN_TAIL_MESSAGES and len(messages) >= MIN_TAIL_MESSAGES:
            # Take at least MIN_TAIL_MESSAGES from the end
            tail_messages = messages[-MIN_TAIL_MESSAGES:]
            tail_tokens = estimate_messages_tokens_rough(tail_messages)
        
        # Middle is everything before tail
        tail_start_idx = len(messages) - len(tail_messages)
        middle_messages = messages[:tail_start_idx]
        
        logger.debug(
            f"Tail protection: {len(tail_messages)} messages, {tail_tokens} tokens "
            f"(budget: {self.tail_token_budget})"
        )
        
        return middle_messages, tail_messages
    
    def _preserve_tool_pairs(
        self,
        middle_messages: List[Dict[str, Any]],
        tail_messages: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Ensure tool-call/result pairs stay together.
        
        If a tool result is in the tail but the corresponding tool call
        is in the middle, move the tool call to the tail.
        
        Args:
            middle_messages: Messages in the middle section
            tail_messages: Messages in the tail section
        
        Returns:
            Tuple of (updated_middle, additional_tail_messages)
        
        Validated by: Property 4 - Tool-Call/Result Pair Integrity
        """
        if not tail_messages or not middle_messages:
            return middle_messages, []
        
        # Find all tool_call_ids in tail (from tool results)
        tail_tool_call_ids: Set[str] = set()
        for msg in tail_messages:
            if msg.get("role") == "tool":
                tool_call_id = msg.get("tool_call_id")
                if tool_call_id:
                    tail_tool_call_ids.add(tool_call_id)
        
        if not tail_tool_call_ids:
            return middle_messages, []
        
        # Find tool calls in middle that need to move to tail
        additional_tail: List[Dict[str, Any]] = []
        remaining_middle: List[Dict[str, Any]] = []
        
        for msg in middle_messages:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                # Check if any tool call matches a tail tool result
                tool_calls = msg.get("tool_calls", [])
                has_matching_call = any(
                    tc.get("id") in tail_tool_call_ids for tc in tool_calls
                )
                
                if has_matching_call:
                    additional_tail.append(msg)
                else:
                    remaining_middle.append(msg)
            else:
                remaining_middle.append(msg)
        
        if additional_tail:
            logger.debug(
                f"Moved {len(additional_tail)} tool call messages to tail "
                f"to preserve pair integrity"
            )
        
        return remaining_middle, additional_tail
    
    def _prune_old_tool_results(
        self,
        messages: List[Dict[str, Any]],
        max_tool_result_tokens: int = 2000,
    ) -> List[Dict[str, Any]]:
        """Prune large tool outputs as a pre-pass before LLM summarization.
        
        This is a cheap operation that removes large tool result content
        before the more expensive LLM summarization step. It preserves
        tool-call/result pair integrity by keeping the tool result message
        but truncating its content.
        
        Args:
            messages: Messages to prune
            max_tool_result_tokens: Maximum tokens per tool result (default 2000)
        
        Returns:
            Messages with large tool results truncated
        
        Requirements: 1.5, 1.7
        """
        if not messages:
            return messages
        
        pruned_messages = []
        pruned_count = 0
        
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("content"):
                # Estimate token count for this tool result
                content = msg.get("content", "")
                if isinstance(content, str):
                    content_tokens = estimate_messages_tokens_rough([msg])
                    
                    if content_tokens > max_tool_result_tokens:
                        # Truncate the content but keep the message
                        # Preserve first ~500 tokens and add truncation notice
                        truncated_content = content[:2000]  # Rough char approximation
                        truncated_msg = msg.copy()
                        truncated_msg["content"] = (
                            f"{truncated_content}\n\n"
                            f"[... Tool output truncated: {content_tokens} tokens reduced to {max_tool_result_tokens} ...]"
                        )
                        pruned_messages.append(truncated_msg)
                        pruned_count += 1
                        logger.debug(
                            f"Pruned tool result: {content_tokens} -> ~{max_tool_result_tokens} tokens"
                        )
                    else:
                        pruned_messages.append(msg)
                else:
                    # Non-string content, keep as-is
                    pruned_messages.append(msg)
            else:
                pruned_messages.append(msg)
        
        if pruned_count > 0:
            logger.info(f"Pruned {pruned_count} large tool results")
        
        return pruned_messages
    
    def _create_truncation_summary(
        self, middle_messages: List[Dict[str, Any]], focus_topic: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a placeholder summary for truncated middle section.
        
        This is a basic fallback implementation used when LLM summarization
        is not available or fails. It creates a simple summary indicating
        what was compressed.
        
        Args:
            middle_messages: Messages being truncated
            focus_topic: Optional topic that was being focused on
        
        Returns:
            Summary message or None
        
        Requirements: 1.8, 1.9
        """
        if not middle_messages:
            return None
        
        # Count message types
        user_count = sum(1 for m in middle_messages if m.get("role") == "user")
        assistant_count = sum(1 for m in middle_messages if m.get("role") == "assistant")
        tool_count = sum(1 for m in middle_messages if m.get("role") == "tool")
        
        # Build summary text
        summary_parts = [
            f"[Context compressed: {len(middle_messages)} messages truncated. "
            f"{user_count} user, {assistant_count} assistant, {tool_count} tool results.]"
        ]
        
        # Include focus topic if provided (Requirement 1.9)
        if focus_topic:
            summary_parts.append(
                f"[Focus topic was: '{focus_topic}' - relevant information may have been prioritized]"
            )
        
        summary_parts.append("[LLM summarization unavailable - basic truncation used]")
        
        summary_text = " ".join(summary_parts)
        
        return {
            "role": "assistant",
            "content": summary_text,
        }
    
    async def _generate_structured_summary(
        self,
        middle_messages: List[Dict[str, Any]],
        focus_topic: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a structured summary of middle messages using LLM.
        
        This method uses an LLM to create a structured summary that preserves
        critical information from the middle section of the conversation.
        If a previous summary exists, it updates it iteratively rather than
        replacing it.
        
        Args:
            middle_messages: Messages to summarize
            focus_topic: Optional topic to prioritize in summarization
        
        Returns:
            Structured summary text or None if summarization fails
        
        Requirements: 1.4, 1.6
        """
        if not middle_messages:
            return None
        
        # Build the summarization prompt
        prompt = self._build_summarization_prompt(middle_messages, focus_topic)
        
        try:
            # Try to use an LLM for summarization
            summary = await self._call_summarization_llm(prompt)
            
            if summary:
                # Update previous summary iteratively (Requirement 1.6)
                # The LLM prompt already includes the previous summary,
                # so the new summary already incorporates it.
                self._previous_summary = summary
                return summary
            
        except Exception as e:
            logger.warning(f"LLM summarization failed, falling back to basic summary: {e}")
        
        # Fallback to basic truncation summary
        return self._create_basic_summary(middle_messages)
    
    def _build_summarization_prompt(
        self,
        messages: List[Dict[str, Any]],
        focus_topic: Optional[str] = None,
    ) -> str:
        """Build the prompt for LLM summarization.
        
        When a focus topic is provided, the prompt instructs the LLM to
        prioritize preserving information related to that topic.
        
        Args:
            messages: Messages to summarize
            focus_topic: Optional topic to prioritize (Requirement 1.9)
        
        Returns:
            Prompt string for the LLM
        """
        # Format messages for the prompt
        formatted_messages = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            # Handle tool calls
            if role == "assistant" and msg.get("tool_calls"):
                tool_calls = msg.get("tool_calls", [])
                tool_info = ", ".join(
                    f"{tc.get('function', {}).get('name', 'unknown')}"
                    for tc in tool_calls
                )
                formatted_messages.append(f"[{role}] Tool calls: {tool_info}")
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "unknown")
                content_preview = str(content)[:200] if content else ""
                formatted_messages.append(f"[{role}] Result for {tool_call_id}: {content_preview}...")
            else:
                content_preview = str(content)[:500] if content else ""
                formatted_messages.append(f"[{role}] {content_preview}...")
        
        messages_text = "\n".join(formatted_messages)
        
        # Build the full prompt
        prompt_parts = [
            "Summarize the following conversation messages into a structured summary.",
            "",
            "Use this template structure:",
            SUMMARY_TEMPLATE,
        ]
        
        # Include previous summary if exists (iterative update)
        if self._previous_summary:
            prompt_parts.extend([
                "",
                "PREVIOUS SUMMARY (update this, don't replace):",
                self._previous_summary,
            ])
        
        # Add focus topic guidance (Requirement 1.9)
        if focus_topic:
            prompt_parts.extend([
                "",
                "=== FOCUS TOPIC ===",
                f"The user is particularly interested in: {focus_topic}",
                "",
                "When creating the summary, prioritize preserving information related to this focus topic:",
                "- In 'Progress', emphasize work related to the focus topic",
                "- In 'Key Decisions', highlight decisions affecting the focus topic",
                "- In 'Relevant Files', list files that relate to the focus topic",
                "- In 'Critical Context', include values/errors relevant to the focus topic",
                "- You may omit details that are clearly unrelated to the focus topic",
            ])
        
        prompt_parts.extend([
            "",
            "MESSAGES TO SUMMARIZE:",
            messages_text,
        ])
        
        return "\n".join(prompt_parts)
    
    async def _call_summarization_llm(self, prompt: str) -> Optional[str]:
        """Call an LLM for summarization using AuxiliaryClient.
        
        This method uses the AuxiliaryClient for summarization, which provides
        automatic provider resolution and fallback on payment/credit errors.
        
        Args:
            prompt: The summarization prompt
        
        Returns:
            Summary text or None if no LLM available
        
        Requirements: 16.10
        """
        try:
            from app.core.auxiliary_client import AuxiliaryClient
            
            # Create auxiliary client with auto provider resolution
            aux_client = AuxiliaryClient(
                provider="auto",
                model="",  # Auto-select based on provider
                base_url=self.base_url,
                api_key=self.api_key,
            )
            
            # Check if auxiliary client is available
            if not aux_client.is_available:
                logger.info("No auxiliary LLM provider available for summarization")
                return None
            
            # Build messages for the LLM
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            
            # Call auxiliary client with fallback support
            response = await aux_client.call_with_fallback(
                messages=messages,
                task="compression",
                max_tokens=2000,
                temperature=0.3,
            )
            
            # Extract content from response
            if hasattr(response, 'choices') and response.choices:
                return response.choices[0].message.content
            
            logger.warning("Auxiliary client returned unexpected response format")
            return None
            
        except ImportError:
            logger.debug("AuxiliaryClient not available for summarization")
        except Exception as e:
            logger.warning(f"Auxiliary client summarization failed: {e}")
        
        # No LLM available
        logger.info("No LLM available for summarization, using basic summary")
        return None
    

    def _create_basic_summary(
        self, messages: List[Dict[str, Any]]
    ) -> str:
        """Create a basic summary without LLM.
        
        This is a fallback when LLM summarization is not available.
        
        Args:
            messages: Messages to summarize
        
        Returns:
            Basic summary text
        """
        # Count message types
        user_count = sum(1 for m in messages if m.get("role") == "user")
        assistant_count = sum(1 for m in messages if m.get("role") == "assistant")
        tool_count = sum(1 for m in messages if m.get("role") == "tool")
        
        # Extract key information
        user_messages = [
            m.get("content", "")[:100]
            for m in messages
            if m.get("role") == "user" and m.get("content")
        ]
        
        # Build basic structured summary
        summary_parts = [
            "## Goal",
            "[Unknown - summarization LLM unavailable]",
            "",
            "## Progress",
            f"### Done",
            f"- {len(messages)} messages processed",
            f"- {user_count} user messages, {assistant_count} assistant messages, {tool_count} tool results",
            "",
            "## Relevant Files",
            "[Could not extract - summarization LLM unavailable]",
            "",
            "## Remaining Work",
            "[Could not determine - summarization LLM unavailable]",
            "",
            "## Critical Context",
            f"Compression performed on {len(messages)} messages",
        ]
        
        if user_messages:
            summary_parts.extend([
                "",
                "### Recent User Messages:",
            ])
            for i, msg in enumerate(user_messages[-3:], 1):
                summary_parts.append(f"{i}. {msg}...")
        
        return "\n".join(summary_parts)
    

    def get_compression_result(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> CompressionResult:
        """Get detailed compression result with metadata (sync version).
        
        Args:
            messages: List of conversation messages
            current_tokens: Optional pre-calculated token count
            focus_topic: Optional topic to prioritize in summarization
        
        Returns:
            CompressionResult with detailed metadata
        """
        if current_tokens is None:
            current_tokens = estimate_messages_tokens_rough(messages)
        
        compressed = self.compress(messages, current_tokens, focus_topic)
        compressed_tokens = estimate_messages_tokens_rough(compressed)
        
        # Count sections
        head_count = min(self.protect_first_n, len(messages))
        
        # Estimate tail count (messages that were protected)
        if len(messages) > head_count:
            _, tail_messages = self._protect_tail_by_budget(messages[head_count:])
            tail_count = len(tail_messages)
        else:
            tail_count = 0
        
        summary_count = len(compressed) - head_count - tail_count
        
        return CompressionResult(
            messages=compressed,
            original_token_count=current_tokens,
            compressed_token_count=compressed_tokens,
            head_count=head_count,
            tail_count=tail_count,
            summary_count=max(0, summary_count),
            was_compressed=len(compressed) < len(messages),
        )
    
    async def get_compression_result_async(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> CompressionResult:
        """Get detailed compression result with metadata (async version with LLM summarization).
        
        Args:
            messages: List of conversation messages
            current_tokens: Optional pre-calculated token count
            focus_topic: Optional topic to prioritize in summarization
        
        Returns:
            CompressionResult with detailed metadata
        
        Requirements: 1.4, 1.6
        """
        if current_tokens is None:
            current_tokens = estimate_messages_tokens_rough(messages)
        
        compressed = await self.compress_async(messages, current_tokens, focus_topic)
        compressed_tokens = estimate_messages_tokens_rough(compressed)
        
        # Count sections
        head_count = min(self.protect_first_n, len(messages))
        
        # Estimate tail count (messages that were protected)
        if len(messages) > head_count:
            _, tail_messages = self._protect_tail_by_budget(messages[head_count:])
            tail_count = len(tail_messages)
        else:
            tail_count = 0
        
        summary_count = len(compressed) - head_count - tail_count
        
        # Include the summary text if available
        summary_text = self._previous_summary
        
        return CompressionResult(
            messages=compressed,
            original_token_count=current_tokens,
            compressed_token_count=compressed_tokens,
            head_count=head_count,
            tail_count=tail_count,
            summary_count=max(0, summary_count),
            was_compressed=len(compressed) < len(messages),
            summary=summary_text,
        )
