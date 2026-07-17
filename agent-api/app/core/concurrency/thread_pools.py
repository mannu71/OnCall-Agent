"""Dedicated thread pools for blocking I/O.

The default asyncio executor is shared by all ``run_in_executor(None, ...)``
callers.  Long-running AWS/boto3 work (CloudWatch Insights polling, Bedrock
streams) can starve short tasks.  Route AWS-bound blocking calls through
``run_in_aws_pool`` instead.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from app.config import settings

aws_pool_executor = ThreadPoolExecutor(
    max_workers=settings.aws_thread_pool_size,
    thread_name_prefix="aws-io",
)

T = TypeVar("T")


async def run_in_aws_pool(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run *fn* in the dedicated AWS/boto3 thread pool."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(
            aws_pool_executor, lambda: fn(*args, **kwargs)
        )
    return await loop.run_in_executor(aws_pool_executor, fn, *args)
