# pyright: reportOptionalCall=false

"""WP-2 — Live trading verification (controlled pilot).

``LiveReadinessService`` performs a *dry-run* verification of every piece that
must be in place before real capital is at risk: broker connectivity, market
data feeds, kill-switch state, the live-mode preflight and an active operator
session. It never places an order — it only reports readiness, so an operator
can rehearse the live path (controlled_live) end to end without exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.operator_session import OperatorSessionService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.risk_service import KillSwitch


@dataclass
class LiveReadinessVerdict:
    """Result of the controlled-pilot verification.

    ``ready`` is true when every *verifiable* live precondition passes;
    ``dry_run`` is always true for this service, and ``live_execution_enabled``
    reports whether the orchestrator is actually in LIVE mode (the one thing a
    dry-run check cannot grant).
    """

    ready: bool
    dry_run: bool = True
    live_execution_enabled: bool = False
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "dry_run": self.dry_run,
            "live_execution_enabled": self.live_execution_enabled,
            "checks": self.checks,
            "reasons": self.reasons,
            "timestamp": self.timestamp.isoformat(),
        }


class LiveReadinessService:
    def __init__(
        self,
        broker: BrokerAdapter | None = None,
        data_ingestion: DataIngestionService | None = None,
        preflight: PreflightService | None = None,
        kill_switch: KillSwitch | None = None,
        operator_session: OperatorSessionService | None = None,
        live_execution_enabled: bool = False,
    ) -> None:
        self._broker = broker
        self._data_ingestion = data_ingestion
        self._preflight = preflight
        self._kill_switch = kill_switch
        self._operator_session = operator_session
        self._live_execution_enabled = live_execution_enabled

    def check(self) -> LiveReadinessVerdict:
        checks: dict[str, bool] = {}
        reasons: list[str] = []

        if self._broker is None:
            checks["broker_connected"] = False
            reasons.append("broker not configured")
        else:
            try:
                balance = self._broker.get_account_balance()
                checks["broker_connected"] = balance > 0
                if not checks["broker_connected"]:
                    reasons.append("broker balance unavailable or non-positive")
            except Exception as exc:  # noqa: BLE001 — connectivity failure is a readiness signal
                checks["broker_connected"] = False
                reasons.append(f"broker unreachable: {exc}")

        if self._data_ingestion is None:
            checks["data_feeds"] = False
            reasons.append("market data not configured")
        else:
            checks["data_feeds"] = len(self._data_ingestion.sources) > 0
            if not checks["data_feeds"]:
                reasons.append("no market data sources configured")

        if self._kill_switch is None:
            checks["kill_switch_closed"] = True
        else:
            verdict = self._kill_switch.can_trade()
            checks["kill_switch_closed"] = verdict.allowed
            if not verdict.allowed:
                reasons.append(f"kill switch engaged: {verdict.reason}")

        if self._preflight is None:
            checks["live_preflight"] = True
        else:
            verdict = self._preflight.check(live_mode=True)
            checks["live_preflight"] = verdict.passed
            reasons.extend(verdict.failures)

        if self._operator_session is None:
            checks["operator_session"] = True
        else:
            checks["operator_session"] = self._operator_session.workflow.session_id is not None
            if not checks["operator_session"]:
                reasons.append("no operator session started (begin the workflow at 'start')")

        ready = len(reasons) == 0
        return LiveReadinessVerdict(
            ready=ready,
            dry_run=True,
            live_execution_enabled=self._live_execution_enabled,
            checks=checks,
            reasons=reasons,
            timestamp=datetime.now(UTC),
        )
