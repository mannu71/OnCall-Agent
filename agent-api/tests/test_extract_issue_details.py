"""
Test script for the new _extract_issue_details method in ReactStrategy.

This script tests both the successful LLM extraction path and the fallback path.
"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.workflow.strategies.react import ReactStrategy


class MockLLMResponse:
    """Mock LLM response object."""
    def __init__(self, content: str):
        self.content = content


async def test_extract_issue_details_success():
    """Test successful extraction of structured issue details."""
    print("\n=== Test 1: Successful LLM Extraction ===")
    
    strategy = ReactStrategy()
    
    # Mock LLM that returns valid JSON
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=MockLLMResponse(
        json.dumps({
            "title": "Database Connection Pool Exhaustion",
            "description": "The application ran out of available database connections due to connection leaks in the user service.",
            "symptoms": [
                "Timeout errors when connecting to database",
                "High number of ESTABLISHED connections",
                "Application becomes unresponsive"
            ],
            "solution": "Implemented proper connection cleanup in finally blocks and reduced pool timeout from 30s to 10s.",
            "category": "database"
        })
    ))
    
    mock_logger = MagicMock()
    
    user_query = "Investigate why the application is timing out when connecting to the database"
    final_answer = "After analyzing the logs, I found that the connection pool was exhausted. The root cause is connection leaks in the user service where connections were not being properly closed. I've implemented proper cleanup in finally blocks and adjusted the pool timeout."
    
    result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    print(f"Title: {result['title']}")
    print(f"Description: {result['description']}")
    print(f"Symptoms: {result['symptoms']}")
    print(f"Solution: {result['solution'][:100]}...")
    print(f"Category: {result['category']}")
    
    assert result['title'] == "Database Connection Pool Exhaustion"
    assert result['category'] == "database"
    assert isinstance(result['symptoms'], list)
    assert len(result['symptoms']) == 3
    print("✓ Test passed!")


async def test_extract_issue_details_with_markdown():
    """Test extraction when LLM returns JSON wrapped in markdown code blocks."""
    print("\n=== Test 2: LLM Returns JSON in Markdown Code Block ===")
    
    strategy = ReactStrategy()
    
    # Mock LLM that returns JSON wrapped in markdown
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=MockLLMResponse(
        "```json\n" + json.dumps({
            "title": "High CPU Usage from Inefficient Query",
            "description": "A poorly optimized SQL query was causing high CPU usage on the database server.",
            "symptoms": ["CPU usage at 95%", "Slow query performance"],
            "solution": "Added index on user_id column and rewrote query to use JOIN instead of subquery.",
            "category": "performance"
        }) + "\n```"
    ))
    
    mock_logger = MagicMock()
    
    user_query = "Check why CPU is so high"
    final_answer = "The issue is caused by an inefficient query. Solution: add index and optimize query."
    
    result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    print(f"Title: {result['title']}")
    print(f"Category: {result['category']}")
    
    assert result['title'] == "High CPU Usage from Inefficient Query"
    assert result['category'] == "performance"
    print("✓ Test passed!")


async def test_extract_issue_details_fallback():
    """Test fallback when LLM returns invalid JSON."""
    print("\n=== Test 3: Fallback on Invalid JSON ===")
    
    strategy = ReactStrategy()
    
    # Mock LLM that returns invalid JSON
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=MockLLMResponse(
        "This is not valid JSON at all!"
    ))
    
    mock_logger = MagicMock()
    
    user_query = "Investigate the network timeout issue"
    final_answer = "The network timeout was caused by a misconfigured firewall rule blocking port 443. Fixed by updating the security group."
    
    result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    print(f"Title: {result['title']}")
    print(f"Description: {result['description'][:50]}...")
    print(f"Symptoms: {result['symptoms']}")
    print(f"Category: {result['category']}")
    
    # Should fall back to basic extraction
    assert result['title'] == user_query[:120]
    assert result['description'] == final_answer[:1000]
    assert result['symptoms'] == [user_query]
    assert result['category'] == "agent_discovered"
    
    # Verify warning was logged
    assert mock_logger.warning.called
    print("✓ Test passed! Fallback worked correctly.")


async def test_fallback_issue_extraction():
    """Test the fallback extraction method directly."""
    print("\n=== Test 4: Direct Fallback Method ===")
    
    strategy = ReactStrategy()
    
    user_query = "Check application logs for errors"
    final_answer = "Found multiple NullPointerException errors in the payment service. Root cause is missing validation on optional fields."
    
    result = strategy._fallback_issue_extraction(user_query, final_answer)
    
    print(f"Title: {result['title']}")
    print(f"Description: {result['description'][:50]}...")
    print(f"Symptoms: {result['symptoms']}")
    print(f"Solution: {result['solution'][:50]}...")
    print(f"Category: {result['category']}")
    
    assert result['title'] == user_query[:120]
    assert result['description'] == final_answer[:1000]
    assert result['symptoms'] == [user_query]
    assert result['solution'] == final_answer[:2000]
    assert result['category'] == "agent_discovered"
    print("✓ Test passed!")


async def test_extract_issue_details_llm_exception():
    """Test fallback when LLM raises an exception."""
    print("\n=== Test 5: Fallback on LLM Exception ===")
    
    strategy = ReactStrategy()
    
    # Mock LLM that raises an exception
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM service unavailable"))
    
    mock_logger = MagicMock()
    
    user_query = "Investigate memory leak"
    final_answer = "Memory leak found in cache implementation. Fixed by adding proper cleanup."
    
    result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    print(f"Title: {result['title']}")
    print(f"Category: {result['category']}")
    
    # Should fall back to basic extraction
    assert result['title'] == user_query[:120]
    assert result['category'] == "agent_discovered"
    
    # Verify warning was logged
    assert mock_logger.warning.called
    print("✓ Test passed! Exception handled gracefully.")


async def main():
    """Run all tests."""
    print("Testing _extract_issue_details implementation")
    print("=" * 60)
    
    try:
        await test_extract_issue_details_success()
        await test_extract_issue_details_with_markdown()
        await test_extract_issue_details_fallback()
        await test_fallback_issue_extraction()
        await test_extract_issue_details_llm_exception()
        
        print("\n" + "=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
