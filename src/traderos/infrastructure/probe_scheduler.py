"""ProbeScheduler — periodic dependency probes paging the real on-call path.

Every ``interval_seconds`` (default 30) the scheduler runs registered probe
functions against the live external dependencies (broker, Vault, PostgreSQL…).
A probe failure on the *rising edge* routes a CRITICAL alert through the real
``OnCallRouter``/``HttpOnCallTransport`` (HTTP 2xx ack, audited + counted, never
a silent drop), and the recovery edge routes a RESOLVED alert. Repeats of an
already-failing probe are suppressed, so a down dependency pages once instead
of alerting every 30 seconds (A7 alert hygiene).

The scheduler owns no domain logic: probes are injected callables returning
:class:`ProbeResult` (the same broker round-trip the operator API exposes),
and the on-call routing uses the exact transport the factory wires. Started as
a daemon thread so a stalled probe can never block shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from traderos.domain.services.notification_service import NotificationLevel
from traderos.infrastructure.notifiers.oncall_router import OnCallDeliveryError

log = logging.getLogger(__name__)

DEFAULT_PROBE_INTERVAL_SECONDS = 30

HEALTHZ_BUDGET_MS = 1500.0
VAULT_BUDGET_MS = 2000.0
BROKER_BUDGET_MS = 1000.0


@dataclass
class ProbeResult:
    """One dependency probe outcome (same shape the operator API reports)."""

    name: str
    ok: bool
    latency_ms: float
    detail: str


def health_probe(
    base_url: str,
    *,
    timeout: float = 2.0,
    budget_ms: float = HEALTHZ_BUDGET_MS,
    path: str = "/v1/healthz",
) -> ProbeResult:
    """Loopback liveness probe: the exact ``/v1/healthz`` route operators read.

    ``ok`` requires a 200 within the latency budget — a hung or restarting API
    edge (503, timeout, connection refused) is a failed probe that pages the
    on-call path like every other dependency.
    """
    start = time.perf_counter()
    try:
        import requests

        resp = requests.get(f"{base_url.rstrip('/')}{path}", timeout=timeout)
        ms = (time.perf_counter() - start) * 1000
        ok = resp.status_code == 200 and ms < budget_ms
        detail = f"status={resp.status_code} in {ms:.1f}ms"
        if not ok:
            over = f" (over {budget_ms:.0f}ms budget)" if ms >= budget_ms else ""
            detail = f"BAD: {detail}{over}"
        return ProbeResult("health", ok, round(ms, 1), detail)
    except Exception as exc:  # noqa: BLE001 — a probe never raises to the caller
        ms = (time.perf_counter() - start) * 1000
        return ProbeResult("health", False, round(ms, 1), f"ERROR: {exc}")


def vault_probe(
    read_fn: Callable[[str], str | None],
    key: str,
    *,
    budget_ms: float = VAULT_BUDGET_MS,
) -> ProbeResult:
    """Secret-manager probe through the circuit-breaked Vault read path.

    ``read_fn`` is the real ``VaultSecretProvider.get`` (wrapped by
    ``VAULT_CB``), so an outage surfaces as a raised fetch error and trips the
    circuit exactly the way the live key path does. ``None`` (missing key) is a
    data outcome, not an outage, and is treated as unhealthy for a probe key
    that must exist: a probe that cannot read its marker is a failed probe.
    """
    start = time.perf_counter()
    try:
        value = read_fn(key)
        ms = (time.perf_counter() - start) * 1000
        ok = value is not None and ms < budget_ms
        detail = f"read in {ms:.1f}ms"
        if value is None:
            detail = f"BAD: read returned no value for '{key}'"
        elif ms >= budget_ms:
            detail = f"BAD: {detail} (over {budget_ms:.0f}ms budget)"
        return ProbeResult("vault", ok, round(ms, 1), detail)
    except Exception as exc:  # noqa: BLE001 — an outage is a failing probe
        ms = (time.perf_counter() - start) * 1000
        return ProbeResult("vault", False, round(ms, 1), f"ERROR: {exc}")


def rate_limit_probe(
    limiter: Any,
    *,
    budget: int = 100,
) -> ProbeResult:
    """Verify the API rate-limiter boundary actually refuses past its budget.

    The probe hammers its OWN synthetic bucket key (never a real client IP or
    shared bucket), so real traffic is never deflated. ``ok=True`` only when
    the limiter refuses at/below the configured ``budget`` (enforcement present).
    ``ok=False`` when it silently admits every request (a limiter that never
    emits 429 is an enforcement regression and must page) or when it refuses
    before budget (a misconfiguration).
    """
    probe_key = f"probe:{uuid.uuid4()}"
    start = time.perf_counter()
    try:
        accepted = 0
        refused = False
        for _ in range(budget + 2):
            if limiter.check(probe_key):
                accepted += 1
            else:
                refused = True
                break
        ms = (time.perf_counter() - start) * 1000
        if refused and budget - 1 <= accepted <= budget:
            return ProbeResult(
                "rate_limit",
                True,
                round(ms, 1),
                f"budget {accepted}/{budget} enforced in {ms:.1f}ms",
            )
        detail = (
            f"BAD: never refused within {budget + 1} requests — 429 not enforced"
            if not refused
            else f"BAD: refused at {accepted} (budget {budget})"
        )
        return ProbeResult("rate_limit", False, round(ms, 1), detail)
    except Exception as exc:  # noqa: BLE001 — a probing exception is a failed probe
        return ProbeResult("rate_limit", False, -1.0, f"ERROR: {exc}")


def broker_health_probe(
    broker: Any,
    mode: Any,
    market_ids: list[uuid.UUID],
) -> ProbeResult:
    """Synthetic probe through the guardrailed broker's public API.

    The exact order path a production paper trade takes — the same
    ``place_limit_order``/``cancel_order`` the cycle executor and the
    guardrail wrapper use — then cancels immediately. No private fields, no
    bypass of the guardrails. In LIVE mode the probe is deliberately read-only
    (a cyclic real-money round-trip is never acceptable); it degrades to a
    connectivity + open-orders round-trip on the same adapters.
    """
    market_id = market_ids[0] if market_ids else uuid.uuid4()
    start = time.perf_counter()
    try:
        if getattr(mode, "value", mode) == "live":
            balance = broker.get_account_balance()
            broker.get_open_orders()
            total_ms = (time.perf_counter() - start) * 1000
            ok = total_ms < 1000.0
            detail = f"balance={balance:.2f} total={total_ms:.1f}ms (live read-only)"
            if ok:
                return ProbeResult("broker", True, round(total_ms, 1), detail)
            return ProbeResult("broker", False, round(total_ms, 1), f"SLOW: {detail}")

        placed = broker.place_limit_order(market_id, "buy", 1.0, 0.01, close_price=None)
        place_ms = (time.perf_counter() - start) * 1000
        if not placed.filled and placed.status != "pending":
            return ProbeResult(
                "broker",
                False,
                round(place_ms, 1),
                f"order rejected by broker: {placed.status}",
            )
        if placed.order_id:
            broker.cancel_order(placed.order_id)
    except Exception as exc:  # noqa: BLE001 — a probe never raises to the caller
        return ProbeResult("broker", False, -1.0, f"ERROR: {exc}")
    total_ms = (time.perf_counter() - start) * 1000
    ok = total_ms < 1000.0
    detail = f"place={place_ms:.1f}ms total={total_ms:.1f}ms"
    if ok:
        return ProbeResult("broker", True, round(total_ms, 1), detail)
    return ProbeResult("broker", False, round(total_ms, 1), f"SLOW: {detail}")


class ProbeScheduler:
    """Periodic probe runner with edge-triggered on-call paging."""

    def __init__(
        self,
        probes: list[Callable[[], ProbeResult]],
        *,
        oncall: Any | None = None,
        audit: Any | None = None,
        metrics: Any | None = None,
        interval_seconds: int = DEFAULT_PROBE_INTERVAL_SECONDS,
    ) -> None:
        self._probes = list(probes)
        self._oncall = oncall
        self._audit = audit
        self._metrics = metrics
        self._interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: dict[str, ProbeResult] = {}

    @property
    def interval_seconds(self) -> int:
        return self._interval

    @property
    def latest(self) -> dict[str, ProbeResult]:
        return dict(self._last)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="probe-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            # Wait for the interval first so short-lived processes (tests,
            # ready-checks) never fire an unsolicited pager.
            if self._stop_event.wait(self._interval):
                break
            try:
                self.run_once()
            except Exception:  # one bad tick must not kill the loop
                log.exception("probe scheduler tick failed")

    def run_once(self) -> dict[str, ProbeResult]:
        """Run every probe once; page on failure/recovery edges. Returns latest."""
        for probe in self._probes:
            start = time.perf_counter()
            try:
                result = probe()
            except Exception as exc:  # noqa: BLE001 — a raising probe is a failed probe
                result = ProbeResult(
                    name=getattr(probe, "__name__", "probe"),
                    ok=False,
                    latency_ms=round((time.perf_counter() - start) * 1000, 1),
                    detail=f"ERROR: {exc}",
                )
            previous = self._last.get(result.name)
            self._last[result.name] = result
            if not result.ok and (previous is None or previous.ok):
                self._page(False, result)
            elif result.ok and previous is not None and not previous.ok:
                self._page(True, result)
            if self._metrics is not None:
                self._metrics.gauge(f"probe.ok.{result.name}", 1.0 if result.ok else 0.0)
        return dict(self._last)

    def _page(self, resolved: bool, result: ProbeResult) -> None:
        if self._audit is not None:
            self._audit.record(
                "probe.recovered" if resolved else "probe.failed",
                "probe-scheduler",
                result.name,
                result.detail,
            )
        if self._metrics is not None:
            self._metrics.counter("probe.recovered" if resolved else "probe.failed", 1.0)
        if self._oncall is None:
            return
        title = (
            f"[RESOLVED] probe recovered: {result.name}"
            if resolved
            else f"Probe failed: {result.name}"
        )
        try:
            self._oncall.route(
                NotificationLevel.CRITICAL,
                title,
                result.detail,
                {"probe": result.name, "latency_ms": round(result.latency_ms, 1)},
            )
        except OnCallDeliveryError as exc:
            log.error("probe page could not be delivered: %s", exc)
