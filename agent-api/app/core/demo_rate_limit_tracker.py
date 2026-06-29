"""Demonstration of rate limit tracker functionality.

This script demonstrates the usage of the rate limit tracking system
with example API response headers.
"""

import time
from rate_limit_tracker import (
    parse_rate_limit_headers,
    format_rate_limit_display,
    format_rate_limit_compact,
)


def demo_anthropic_headers():
    """Demonstrate parsing Anthropic-style rate limit headers."""
    print("=" * 80)
    print("DEMO: Anthropic Rate Limit Headers")
    print("=" * 80)
    
    # Simulate Anthropic API response headers
    headers = {
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-requests": "30",
        "x-ratelimit-reset-requests": "60.0",
        "x-ratelimit-limit-tokens": "40000",
        "x-ratelimit-remaining-tokens": "20000",
        "x-ratelimit-reset-tokens": "60.0",
    }
    
    state = parse_rate_limit_headers(headers, provider="anthropic")
    
    if state:
        print("\nFull Display:")
        print(format_rate_limit_display(state))
        print("\nCompact Display:")
        print(format_rate_limit_compact(state))
    else:
        print("No rate limit data found")


def demo_high_usage_warning():
    """Demonstrate warning display when usage is high."""
    print("\n" + "=" * 80)
    print("DEMO: High Usage Warning (>80%)")
    print("=" * 80)
    
    # Simulate headers with high usage
    headers = {
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-requests": "5",  # 90% used
        "x-ratelimit-reset-requests": "45.0",
        "x-ratelimit-limit-tokens": "100000",
        "x-ratelimit-remaining-tokens": "10000",  # 90% used
        "x-ratelimit-reset-tokens": "45.0",
    }
    
    state = parse_rate_limit_headers(headers, provider="openai")
    
    if state:
        print("\nFull Display:")
        print(format_rate_limit_display(state))
        print("\nCompact Display:")
        print(format_rate_limit_compact(state))


def demo_all_buckets():
    """Demonstrate all four rate limit buckets."""
    print("\n" + "=" * 80)
    print("DEMO: All Four Rate Limit Buckets")
    print("=" * 80)
    
    # Simulate headers with all four buckets
    headers = {
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-requests": "35",
        "x-ratelimit-reset-requests": "60.0",
        "x-ratelimit-limit-requests-1h": "1000",
        "x-ratelimit-remaining-requests-1h": "750",
        "x-ratelimit-reset-requests-1h": "3600.0",
        "x-ratelimit-limit-tokens": "40000",
        "x-ratelimit-remaining-tokens": "25000",
        "x-ratelimit-reset-tokens": "60.0",
        "x-ratelimit-limit-tokens-1h": "2000000",
        "x-ratelimit-remaining-tokens-1h": "1500000",
        "x-ratelimit-reset-tokens-1h": "3600.0",
    }
    
    state = parse_rate_limit_headers(headers, provider="anthropic")
    
    if state:
        print("\nFull Display:")
        print(format_rate_limit_display(state))
        print("\nCompact Display:")
        print(format_rate_limit_compact(state))


def demo_time_adjustment():
    """Demonstrate time adjustment for elapsed time."""
    print("\n" + "=" * 80)
    print("DEMO: Time Adjustment (Simulating Elapsed Time)")
    print("=" * 80)
    
    headers = {
        "x-ratelimit-limit-requests": "50",
        "x-ratelimit-remaining-requests": "30",
        "x-ratelimit-reset-requests": "60.0",
    }
    
    state = parse_rate_limit_headers(headers, provider="test")
    
    if state:
        print("\nInitial state:")
        print(format_rate_limit_display(state))
        
        print("\nWaiting 2 seconds...")
        time.sleep(2)
        
        print("\nAfter 2 seconds (reset time should decrease):")
        print(format_rate_limit_display(state))


if __name__ == "__main__":
    demo_anthropic_headers()
    demo_high_usage_warning()
    demo_all_buckets()
    demo_time_adjustment()
    
    print("\n" + "=" * 80)
    print("Demo complete!")
    print("=" * 80)
