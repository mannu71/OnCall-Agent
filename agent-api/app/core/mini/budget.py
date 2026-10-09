"""CPU budget for local model inference.

``os.cpu_count()`` reports the host's cores and ignores the container's CPU
quota, so a container limited to 2 CPUs on a 16-core host would start 16
inference threads and starve the event loop. These helpers read the cgroup
quota (v2, then v1) and the affinity mask, take the smallest, and reserve
cores for the API process itself.
"""
from __future__ import annotations

import math
import os
from typing import Optional

_CGROUP_V2_MAX = "/sys/fs/cgroup/cpu.max"
_CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
_CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def cgroup_cpu_limit(v2_path: str = _CGROUP_V2_MAX, v1_quota: str = _CGROUP_V1_QUOTA,
                     v1_period: str = _CGROUP_V1_PERIOD) -> Optional[float]:
    """CPUs allowed by the cgroup quota, or ``None`` when unlimited/unknown."""
    v2 = _read(v2_path)
    if v2:
        parts = v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                return int(parts[0]) / int(parts[1])
            except (ValueError, ZeroDivisionError):
                return None
        return None
    quota, period = _read(v1_quota), _read(v1_period)
    try:
        if quota and period and int(quota) > 0:
            return int(quota) / int(period)
    except (ValueError, ZeroDivisionError):
        return None
    return None


def available_cpus() -> int:
    """Whole CPUs this process may actually use (at least 1)."""
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = os.cpu_count() or 1
    limit = cgroup_cpu_limit()
    cpus = min(affinity, math.floor(limit)) if limit else affinity
    return max(1, cpus)


def inference_threads(reserved: int = 1, cpus: Optional[int] = None) -> int:
    """Threads to give local inference after reserving cores for the API (>= 1)."""
    total = available_cpus() if cpus is None else cpus
    return max(1, total - max(0, reserved))
