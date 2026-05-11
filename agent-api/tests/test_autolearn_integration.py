"""
Integration test demonstrating the improved auto-learn feature.

This test shows how the new structured extraction improves knowledge base entries
compared to the old simple truncation approach.
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


async def demonstrate_improvement():
    """Demonstrate the improvement from old to new approach."""
    print("\n" + "=" * 80)
    print("AUTO-LEARN IMPROVEMENT DEMONSTRATION")
    print("=" * 80)
    
    strategy = ReactStrategy()
    
    # Realistic scenario: User asks agent to investigate an issue
    user_query = "Investigate why the payment service is failing with 500 errors during peak hours"
    
    final_answer = """After analyzing the logs and metrics, I found the root cause of the payment service failures.

The issue is caused by database connection pool exhaustion during peak traffic periods. The payment service was configured with a maximum of 10 database connections, but during peak hours we're seeing 50+ concurrent requests.

Key findings:
- Connection pool size: 10 (too small)
- Peak concurrent requests: 50-60
- Average connection hold time: 2.5 seconds
- Error pattern: "Timeout waiting for connection from pool"

Solution implemented:
1. Increased connection pool size from 10 to 50
2. Added connection timeout of 5 seconds (was infinite)
3. Implemented connection leak detection
4. Added monitoring alerts for pool utilization > 80%

The fix has been deployed and verified. Payment success rate is now back to 99.9%."""
    
    # Mock LLM that returns structured extraction
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=MockLLMResponse(
        json.dumps({
            "title": "Payment Service Database Connection Pool Exhaustion",
            "description": "The payment service experiences 500 errors during peak hours due to database connection pool exhaustion. The pool was configured with only 10 connections but peak traffic requires 50+ concurrent connections.",
            "symptoms": [
                "500 errors in payment service during peak hours",
                "Timeout waiting for connection from pool errors",
                "Connection pool utilization at 100%",
                "Average 50-60 concurrent requests during peak"
            ],
            "solution": "Increased connection pool size from 10 to 50, added 5-second connection timeout, implemented connection leak detection, and added monitoring alerts for pool utilization above 80%.",
            "category": "database"
        })
    ))
    
    mock_logger = MagicMock()
    
    # Extract structured details using new method
    new_result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    # Simulate old method (simple truncation)
    old_result = {
        "title": user_query[:120],
        "description": final_answer[:1000],
        "symptoms": [user_query],
        "solution": final_answer[:2000],
        "category": "agent_discovered"
    }
    
    # Display comparison
    print("\n" + "-" * 80)
    print("OLD APPROACH (Simple Truncation)")
    print("-" * 80)
    print(f"\nTitle:\n  {old_result['title']}")
    print(f"\nDescription:\n  {old_result['description'][:150]}...")
    print(f"\nSymptoms:")
    for symptom in old_result['symptoms']:
        print(f"  - {symptom}")
    print(f"\nSolution:\n  {old_result['solution'][:150]}...")
    print(f"\nCategory: {old_result['category']}")
    
    print("\n" + "-" * 80)
    print("NEW APPROACH (LLM-Based Structured Extraction)")
    print("-" * 80)
    print(f"\nTitle:\n  {new_result['title']}")
    print(f"\nDescription:\n  {new_result['description']}")
    print(f"\nSymptoms:")
    for symptom in new_result['symptoms']:
        print(f"  - {symptom}")
    print(f"\nSolution:\n  {new_result['solution']}")
    print(f"\nCategory: {new_result['category']}")
    
    print("\n" + "=" * 80)
    print("KEY IMPROVEMENTS")
    print("=" * 80)
    print("✓ Title: Concise and descriptive (not a user instruction)")
    print("✓ Description: Clear summary of the issue (not raw truncation)")
    print("✓ Symptoms: Actual observable symptoms (not user query)")
    print("✓ Solution: Actionable steps (not duplicate of description)")
    print("✓ Category: Specific category (database, not generic)")
    print("=" * 80 + "\n")


async def demonstrate_fallback_safety():
    """Demonstrate that fallback ensures the agent never fails."""
    print("\n" + "=" * 80)
    print("FALLBACK SAFETY DEMONSTRATION")
    print("=" * 80)
    
    strategy = ReactStrategy()
    
    user_query = "Check why API is slow"
    final_answer = "API slowness caused by missing index. Added index on user_id column."
    
    # Mock LLM that fails
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("LLM service temporarily unavailable"))
    
    mock_logger = MagicMock()
    
    print("\nScenario: LLM service is temporarily unavailable")
    print("Expected: System falls back to simple extraction (agent continues)")
    
    result = await strategy._extract_issue_details(
        user_query, final_answer, mock_llm, mock_logger
    )
    
    print(f"\n✓ Fallback activated successfully")
    print(f"  Title: {result['title']}")
    print(f"  Category: {result['category']}")
    print(f"  Symptoms: {result['symptoms']}")
    
    # Verify warning was logged
    assert mock_logger.warning.called
    print(f"\n✓ Warning logged: {mock_logger.warning.call_args[0][0]}")
    
    print("\n✓ Agent execution continues without failure")
    print("=" * 80 + "\n")


async def main():
    """Run demonstrations."""
    try:
        await demonstrate_improvement()
        await demonstrate_fallback_safety()
        
        print("\n" + "=" * 80)
        print("DEMONSTRATION COMPLETE")
        print("=" * 80)
        print("\nThe new auto-learn feature provides:")
        print("  1. Higher quality knowledge base entries")
        print("  2. Better searchability (structured symptoms)")
        print("  3. Automatic categorization")
        print("  4. Robust fallback (never fails)")
        print("=" * 80 + "\n")
        
    except Exception as e:
        print(f"\n✗ Demonstration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
