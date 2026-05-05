"""Unit tests for usage endpoint.

Tests the /usage/rate-limits endpoint that exposes rate limit state
from ReactStrategy executions.

**Validates: Requirements 5.5, 5.6**
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
import time

from app.main import app
from app.core.rate_limit_tracker import RateLimitState, RateLimitBucket
from app.api.v1.endpoints.usage import update_rate_limit_state, get_rate_limit_state


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def sample_rate_limit_state():
    """Create a sample rate limit state for testing."""
    now = time.time()
    return RateLimitState(
        requests_min=RateLimitBucket(
            limit=50,
            remaining=45,
            reset_seconds=30.0,
            captured_at=now,
        ),
        requests_hour=RateLimitBucket(
            limit=500,
            remaining=450,
            reset_seconds=1800.0,
            captured_at=now,
        ),
        tokens_min=RateLimitBucket(
            limit=10000,
            remaining=9500,
            reset_seconds=30.0,
            captured_at=now,
        ),
        tokens_hour=RateLimitBucket(
            limit=100000,
            remaining=95000,
            reset_seconds=1800.0,
            captured_at=now,
        ),
        captured_at=now,
        provider="anthropic",
    )


@pytest.fixture(autouse=True)
def reset_rate_limit_state():
    """Reset global rate limit state before each test."""
    update_rate_limit_state(None)
    yield
    update_rate_limit_state(None)


def test_get_rate_limits_no_data(client):
    """Test GET /usage/rate-limits when no data is available.
    
    **Validates: Requirements 5.5**
    """
    response = client.get("/api/v1/usage/rate-limits")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["has_data"] is False
    assert data["display"] == "No rate limit data available"
    assert data["compact"] == "No rate limits"
    assert data["provider"] is None
    assert data["requests_min"] is None
    assert data["requests_hour"] is None
    assert data["tokens_min"] is None
    assert data["tokens_hour"] is None


def test_get_rate_limits_with_data(client, sample_rate_limit_state):
    """Test GET /usage/rate-limits with rate limit data.
    
    **Validates: Requirements 5.5, 5.6**
    """
    # Update global state
    update_rate_limit_state(sample_rate_limit_state)
    
    response = client.get("/api/v1/usage/rate-limits")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify basic fields
    assert data["has_data"] is True
    assert data["provider"] == "anthropic"
    assert data["captured_at"] == sample_rate_limit_state.captured_at
    
    # Verify requests_min bucket
    assert data["requests_min"] is not None
    assert data["requests_min"]["limit"] == 50
    assert data["requests_min"]["remaining"] == 45
    assert data["requests_min"]["used"] == 5
    assert data["requests_min"]["usage_pct"] == 0.1
    
    # Verify requests_hour bucket
    assert data["requests_hour"] is not None
    assert data["requests_hour"]["limit"] == 500
    assert data["requests_hour"]["remaining"] == 450
    assert data["requests_hour"]["used"] == 50
    assert data["requests_hour"]["usage_pct"] == 0.1
    
    # Verify tokens_min bucket
    assert data["tokens_min"] is not None
    assert data["tokens_min"]["limit"] == 10000
    assert data["tokens_min"]["remaining"] == 9500
    assert data["tokens_min"]["used"] == 500
    assert data["tokens_min"]["usage_pct"] == 0.05
    
    # Verify tokens_hour bucket
    assert data["tokens_hour"] is not None
    assert data["tokens_hour"]["limit"] == 100000
    assert data["tokens_hour"]["remaining"] == 95000
    assert data["tokens_hour"]["used"] == 5000
    assert data["tokens_hour"]["usage_pct"] == 0.05
    
    # Verify display strings are present
    assert data["display"] is not None
    assert "anthropic" in data["display"].lower()
    assert data["compact"] is not None
    assert "anthropic" in data["compact"].lower()
    
    # No warnings (usage < 80%)
    assert len(data["warnings"]) == 0


def test_get_rate_limits_with_warnings(client):
    """Test GET /usage/rate-limits with high usage triggering warnings.
    
    **Validates: Requirements 5.6**
    """
    now = time.time()
    
    # Create state with high usage (>80%)
    state = RateLimitState(
        requests_min=RateLimitBucket(
            limit=50,
            remaining=8,  # 84% used
            reset_seconds=30.0,
            captured_at=now,
        ),
        requests_hour=RateLimitBucket(
            limit=500,
            remaining=450,
            reset_seconds=1800.0,
            captured_at=now,
        ),
        tokens_min=RateLimitBucket(
            limit=10000,
            remaining=1000,  # 90% used
            reset_seconds=30.0,
            captured_at=now,
        ),
        tokens_hour=RateLimitBucket(
            limit=100000,
            remaining=95000,
            reset_seconds=1800.0,
            captured_at=now,
        ),
        captured_at=now,
        provider="anthropic",
    )
    
    update_rate_limit_state(state)
    
    response = client.get("/api/v1/usage/rate-limits")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify warnings are present
    assert len(data["warnings"]) == 2
    assert any("Requests/min" in w and "84%" in w for w in data["warnings"])
    assert any("Tokens/min" in w and "90%" in w for w in data["warnings"])


def test_get_rate_limits_display_no_data(client):
    """Test GET /usage/rate-limits/display when no data is available.
    
    **Validates: Requirements 5.5, 5.7**
    """
    response = client.get("/api/v1/usage/rate-limits/display")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["display"] == "No rate limit data available"
    assert data["compact"] == "No rate limits"


def test_get_rate_limits_display_with_data(client, sample_rate_limit_state):
    """Test GET /usage/rate-limits/display with rate limit data.
    
    **Validates: Requirements 5.5, 5.7**
    """
    update_rate_limit_state(sample_rate_limit_state)
    
    response = client.get("/api/v1/usage/rate-limits/display")
    
    assert response.status_code == 200
    data = response.json()
    
    # Verify display string contains expected elements
    assert "anthropic" in data["display"].lower()
    assert "requests/min" in data["display"].lower()
    assert "tokens/min" in data["display"].lower()
    
    # Verify compact string
    assert "anthropic" in data["compact"].lower()
    assert "%" in data["compact"]


def test_update_and_get_rate_limit_state(sample_rate_limit_state):
    """Test update_rate_limit_state and get_rate_limit_state functions.
    
    **Validates: Requirements 5.5**
    """
    # Initially no data
    assert get_rate_limit_state() is None
    
    # Update state
    update_rate_limit_state(sample_rate_limit_state)
    
    # Verify state is stored
    state = get_rate_limit_state()
    assert state is not None
    assert state.provider == "anthropic"
    assert state.requests_min.limit == 50
    
    # Clear state
    update_rate_limit_state(None)
    
    # Verify state is cleared
    assert get_rate_limit_state() is None


def test_rate_limit_response_from_state(sample_rate_limit_state):
    """Test RateLimitResponse.from_state conversion.
    
    **Validates: Requirements 5.5, 5.6**
    """
    from app.api.v1.endpoints.usage import RateLimitResponse
    
    response = RateLimitResponse.from_state(sample_rate_limit_state)
    
    assert response.has_data is True
    assert response.provider == "anthropic"
    assert response.requests_min is not None
    assert response.requests_min.limit == 50
    assert response.display is not None
    assert response.compact is not None
    assert len(response.warnings) == 0


def test_rate_limit_response_no_data():
    """Test RateLimitResponse.no_data factory method.
    
    **Validates: Requirements 5.5**
    """
    from app.api.v1.endpoints.usage import RateLimitResponse
    
    response = RateLimitResponse.no_data()
    
    assert response.has_data is False
    assert response.provider is None
    assert response.requests_min is None
    assert response.display == "No rate limit data available"
    assert response.compact == "No rate limits"


def test_rate_limit_bucket_response_from_bucket(sample_rate_limit_state):
    """Test RateLimitBucketResponse.from_bucket conversion.
    
    **Validates: Requirements 5.5**
    """
    from app.api.v1.endpoints.usage import RateLimitBucketResponse
    
    bucket = sample_rate_limit_state.requests_min
    response = RateLimitBucketResponse.from_bucket(bucket)
    
    assert response.limit == 50
    assert response.remaining == 45
    assert response.used == 5
    assert response.usage_pct == 0.1
    assert response.reset_seconds >= 0  # Adjusted for elapsed time


def test_endpoint_with_partial_data(client):
    """Test endpoint with partial rate limit data (only some buckets).
    
    **Validates: Requirements 5.5**
    """
    now = time.time()
    
    # Create state with only requests_min bucket
    state = RateLimitState(
        requests_min=RateLimitBucket(
            limit=50,
            remaining=45,
            reset_seconds=30.0,
            captured_at=now,
        ),
        requests_hour=RateLimitBucket(
            limit=0,  # No data
            remaining=0,
            reset_seconds=0.0,
            captured_at=now,
        ),
        tokens_min=RateLimitBucket(
            limit=0,  # No data
            remaining=0,
            reset_seconds=0.0,
            captured_at=now,
        ),
        tokens_hour=RateLimitBucket(
            limit=0,  # No data
            remaining=0,
            reset_seconds=0.0,
            captured_at=now,
        ),
        captured_at=now,
        provider="openai",
    )
    
    update_rate_limit_state(state)
    
    response = client.get("/api/v1/usage/rate-limits")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["has_data"] is True
    assert data["provider"] == "openai"
    assert data["requests_min"] is not None
    assert data["requests_hour"] is None  # No data for this bucket
    assert data["tokens_min"] is None  # No data for this bucket
    assert data["tokens_hour"] is None  # No data for this bucket
