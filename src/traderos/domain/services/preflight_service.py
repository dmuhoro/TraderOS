from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime

from traderos.domain.ports import AuditPort
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.risk_service import KillSwitch


@dataclass
class PreflightVerdict:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __bool__(self) -> bool:
        return self.passed


_LIVE_CONFIRM_ENV = "LIVE_TRADING_CONFIRMED"


class PreflightService:
    def __init__(
        self,
        audit: AuditPort | None = None,
        broker_reconciliation: BrokerStateReconciliationService | None = None,
        kill_switch: KillSwitch | None = None,
        allowed_markets: frozenset | None = None,
        require_allowlist: bool = False,
    ) -> None:
        self._audit = audit
        self._broker_reconciliation = broker_reconciliation
        self._kill_switch = kill_switch
        self._allowed_markets = allowed_markets
        self._require_allowlist = require_allowlist

    def check(self, live_mode: bool = False) -> PreflightVerdict:
        checks: dict[str, bool] = {}
        failures: list[str] = []

        if self._audit is not None:
            chain_ok = self._audit.verify_chain()
            checks["audit_chain"] = chain_ok
            if not chain_ok:
                failures.append("Audit chain verification failed")
        else:
            checks["audit_chain"] = True

        if self._broker_reconciliation is not None:
            reconciled = self._broker_reconciliation.can_accept_orders
            checks["broker_reconciliation"] = reconciled
            if not reconciled:
                failures.append("Broker state reconciliation incomplete — order acceptance blocked")
        else:
            checks["broker_reconciliation"] = True

        if self._kill_switch is not None:
            verdict = self._kill_switch.can_trade()
            ks_ok = verdict.allowed
            checks["kill_switch"] = ks_ok
            if not ks_ok:
                failures.append(f"Kill switch engaged: {verdict.reason}")
        else:
            checks["kill_switch"] = True

        if live_mode:
            live_confirmed = os.getenv(_LIVE_CONFIRM_ENV, "").lower() in ("true", "1", "yes")
            checks["live_trading_confirmed"] = live_confirmed
            if not live_confirmed:
                failures.append(
                    f"Live mode requires {_LIVE_CONFIRM_ENV}=true "
                    "(explicit confirmation beyond env-var presence)"
                )
            if self._require_allowlist and not self._allowed_markets:
                checks["market_allowlist"] = False
                failures.append(
                    "Live mode requires a non-empty market allowlist "
                    "(risk.allowed_markets) — fail-closed"
                )
            else:
                checks["market_allowlist"] = True
        else:
            checks["live_trading_confirmed"] = True

        passed = len(failures) == 0
        return PreflightVerdict(
            passed=passed,
            checks=checks,
            failures=failures,
            timestamp=datetime.now(UTC),
        )
