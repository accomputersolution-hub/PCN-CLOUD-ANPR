from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class AgentHealth:
    pid: int
    cpu_usage: float | None
    memory_usage: float | None
    queue_size: int


def sample_health(queue_size: int) -> AgentHealth:
    cpu = mem = None
    try:
        import psutil  # optional

        proc = psutil.Process(os.getpid())
        cpu = proc.cpu_percent(interval=0.0)
        mem = proc.memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    return AgentHealth(pid=os.getpid(), cpu_usage=cpu, memory_usage=mem, queue_size=queue_size)
