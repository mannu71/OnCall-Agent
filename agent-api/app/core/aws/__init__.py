"""AWS integration helpers: credential resolution and CloudWatch caching/rate-limiting.

Modules: aws_credentials, cloudwatch_cache, cloudwatch_ratelimit, trace_ids.
Import them directly (e.g. ``from app.core.aws import aws_credentials``); this
package intentionally re-exports nothing to avoid import cycles.
"""
