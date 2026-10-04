from __future__ import annotations

import argparse
import json
import os
import platform
import time
import uuid
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from math import ceil
from statistics import median

from traderos.domain.entities import OHLCV
from traderos.domain.entities import Candle
from traderos.domain.entities import Timeframe
from traderos.domain.services.analysis_service import AnalysisService

BUDGET_SECONDS = 1.0  # Same strict budget as tests/test_analysis_performance.py:81.


def _system_cpu_counters() -> tuple[int, int] | None:
    """Return aggregate Linux CPU (total, idle) jiffies when available."""
    try:
        with open("/proc/stat", encoding="ascii") as cpu_stat:
            fields = cpu_stat.readline().split()
        counters = [int(value) for value in fields[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if len(counters) < 4:
        return None
    idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
    return sum(counters), idle


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Repeated 5,000-candle indicator benchmark")
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=21)
    parser.add_argument("--no-pin", action="store_true", help="do not pin this process to one CPU")
    args = parser.parse_args()
    if args.warmups < 1 or args.iterations < 2:
        parser.error("warmups must be positive and iterations must be at least 2")

    host_cpu_count = os.cpu_count()
    pinned_cpu: int | None = None
    if not args.no_pin and hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        allowed_cpus = os.sched_getaffinity(0)
        if allowed_cpus:
            pinned_cpu = min(allowed_cpus)
            os.sched_setaffinity(0, {pinned_cpu})

    market_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    candles = [
        Candle(
            market_id,
            OHLCV(
                Decimal(close),
                Decimal(close + 2),
                Decimal(close - 2),
                Decimal(close),
                Decimal(1000),
            ),
            datetime(2024, 1, 1, tzinfo=UTC) + timedelta(minutes=index),
            Timeframe.MINUTE_1,
        )
        for index in range(5000)
        for close in (100 + (index * 17) % 101,)
    ]
    calculations = (
        lambda: AnalysisService.compute_sma(candles, 20),
        lambda: AnalysisService.compute_ema(candles, 20),
        lambda: AnalysisService.compute_rsi(candles, 14),
        lambda: AnalysisService.compute_atr(candles, 14),
        lambda: AnalysisService.compute_bollinger_bands(candles, 20),
        lambda: AnalysisService.compute_stochastics(candles, 14, 3),
    )
    for _ in range(args.warmups):
        for calculate in calculations:
            calculate()

    cpu_before = _system_cpu_counters()
    process_before = time.process_time()
    elapsed_samples: list[float] = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        for calculate in calculations:
            calculate()
        elapsed_samples.append(time.perf_counter() - started)
    process_cpu_seconds = time.process_time() - process_before
    cpu_after = _system_cpu_counters()
    wall_seconds = sum(elapsed_samples)

    system_cpu_busy_percent: float | None = None
    estimated_other_cpu_cores: float | None = None
    other_load_detected: bool | None = None
    if cpu_before is not None and cpu_after is not None and host_cpu_count:
        total_delta = cpu_after[0] - cpu_before[0]
        idle_delta = cpu_after[1] - cpu_before[1]
        if total_delta > 0:
            busy_delta = max(0, total_delta - idle_delta)
            system_cpu_busy_percent = busy_delta / total_delta * 100
            estimated_other_cpu_cores = max(
                0.0,
                busy_delta / total_delta * host_cpu_count - process_cpu_seconds / wall_seconds,
            )
            other_load_detected = estimated_other_cpu_cores > 0.1

    p50 = median(elapsed_samples)
    p95 = _percentile(elapsed_samples, 0.95)
    print(
        json.dumps(
            {
                "candles": len(candles),
                "measurement": "same six-indicator batch as tests/test_analysis_performance.py",
                "warmups": args.warmups,
                "iterations": args.iterations,
                "seconds": {
                    "min": round(min(elapsed_samples), 6),
                    "p50": round(p50, 6),
                    "p95": round(p95, 6),
                    "max": round(max(elapsed_samples), 6),
                },
                "budget_seconds": BUDGET_SECONDS,
                "budget_met_at_p50": p50 < BUDGET_SECONDS,
                "budget_met_at_p95": p95 < BUDGET_SECONDS,
                "python": platform.python_version(),
                "cpu_count": host_cpu_count,
                "cpu_affinity": (
                    sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
                ),
                "pinned_cpu": pinned_cpu,
                "system_cpu_busy_percent": (
                    round(system_cpu_busy_percent, 2)
                    if system_cpu_busy_percent is not None
                    else None
                ),
                "estimated_other_cpu_core_equivalents": (
                    round(estimated_other_cpu_cores, 3)
                    if estimated_other_cpu_cores is not None
                    else None
                ),
                "other_load_detected": other_load_detected,
                "indicators": [
                    "compute_sma",
                    "compute_ema",
                    "compute_rsi",
                    "compute_atr",
                    "compute_bollinger_bands",
                    "compute_stochastics",
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
