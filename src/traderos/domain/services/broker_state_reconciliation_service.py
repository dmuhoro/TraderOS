from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime

from traderos.domain.ports import BrokerPort


@dataclass
class BrokerReconciliationResult:
    matched_positions: int = 0
    reconciled_positions: int = 0
    errors: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class BrokerStateReconciliationService:
    def __init__(self, broker: BrokerPort) -> None:
        self._broker = broker
        self._startup_reconciled: bool = False
        self._reconciled_at: datetime | None = None
        self._consecutive_failures: int = 0

    @property
    def startup_reconciled(self) -> bool:
        return self._startup_reconciled

    @property
    def reconciled_at(self) -> datetime | None:
        return self._reconciled_at

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def can_accept_orders(self) -> bool:
        return self._startup_reconciled

    def reconcile(self) -> BrokerReconciliationResult:
        errors: list[str] = []
        matched_positions = 0
        reconciled_positions = 0

        try:
            broker_positions = self._broker.get_positions()
            broker_orders = self._broker.get_open_orders()
        except (RuntimeError, ValueError, OSError) as e:
            errors.append(f"Failed to fetch broker state: {e}")
            self._consecutive_failures += 1
            return BrokerReconciliationResult(
                matched_positions=0,
                reconciled_positions=0,
                errors=errors,
                timestamp=datetime.now(UTC),
            )

        matched_positions = len(broker_positions)
        reconciled_positions = len(broker_positions) + len(broker_orders)

        if not errors:
            self._startup_reconciled = True
            self._reconciled_at = datetime.now(UTC)
            self._consecutive_failures = 0

        return BrokerReconciliationResult(
            matched_positions=matched_positions,
            reconciled_positions=reconciled_positions,
            errors=errors,
            timestamp=datetime.now(UTC),
        )
