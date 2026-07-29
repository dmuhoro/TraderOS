from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from enum import Enum
from typing import Any


class MismatchType(Enum):
    BROKER_ONLY_POSITION = "broker_only_position"
    LOCAL_ONLY_POSITION = "local_only_position"
    QUANTITY_MISMATCH = "quantity_mismatch"
    PRICE_MISMATCH = "price_mismatch"
    BROKER_ONLY_ORDER = "broker_only_order"
    LOCAL_ONLY_ORDER = "local_only_order"
    STALE_SNAPSHOT = "stale_snapshot"
    DUPLICATE_BROKER_STATE = "duplicate_broker_state"
    BROKER_FAILURE = "broker_failure"
    UNKNOWN_STATE = "unknown_state"


@dataclass
class MismatchDetail:
    mismatch_type: MismatchType
    description: str
    severity: int = 1  # 1=warning, 2=error, 3=critical


@dataclass
class BrokerReconciliationResult:
    matched_positions: int = 0
    reconciled_positions: int = 0
    errors: list[str] = field(default_factory=list)
    mismatches: list[MismatchDetail] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def has_mismatches(self) -> bool:
        return len(self.mismatches) > 0

    @property
    def failed(self) -> bool:
        return len(self.errors) > 0 or any(m.severity >= 2 for m in self.mismatches)


_STALE_THRESHOLD_SECONDS = 300


class BrokerStateReconciliationService:
    def __init__(self, broker: Any) -> None:
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

    def reconcile(
        self,
        local_positions: list[dict] | None = None,
        local_orders: list[dict] | None = None,
    ) -> BrokerReconciliationResult:
        errors: list[str] = []
        mismatches: list[MismatchDetail] = []
        matched_positions = 0
        reconciled_positions = 0

        try:
            broker_positions = self._broker.get_positions()
            broker_orders = self._broker.get_open_orders()
        except (RuntimeError, ValueError, OSError) as e:
            errors.append(f"Failed to fetch broker state: {e}")
            mismatches.append(MismatchDetail(MismatchType.BROKER_FAILURE, str(e), severity=3))
            self._consecutive_failures += 1
            return BrokerReconciliationResult(
                matched_positions=0,
                reconciled_positions=0,
                errors=errors,
                mismatches=mismatches,
                timestamp=datetime.now(UTC),
            )

        broker_positions = broker_positions or []
        broker_orders = broker_orders or []
        local_positions = local_positions or []
        local_orders = local_orders or []

        matched_positions = min(len(broker_positions), len(local_positions))

        broker_pos_by_key = self._index_positions(broker_positions)
        local_pos_by_key = self._index_positions(local_positions)

        all_position_keys = set(broker_pos_by_key) | set(local_pos_by_key)

        for key in all_position_keys:
            bp = broker_pos_by_key.get(key)
            lp = local_pos_by_key.get(key)

            if bp is not None and lp is None:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.BROKER_ONLY_POSITION,
                        f"Broker has position {key} not found locally",
                        severity=2,
                    )
                )
            elif lp is not None and bp is None:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.LOCAL_ONLY_POSITION,
                        f"Local has position {key} not found on broker",
                        severity=2,
                    )
                )
            elif bp is not None and lp is not None:
                bp_qty = float(bp.get("qty", bp.get("quantity", 0)))
                lp_qty = float(lp.get("qty", lp.get("quantity", 0)))
                if abs(bp_qty - lp_qty) > 0.0001:
                    mismatches.append(
                        MismatchDetail(
                            MismatchType.QUANTITY_MISMATCH,
                            f"Quantity mismatch for {key}: broker={bp_qty} local={lp_qty}",
                            severity=2,
                        )
                    )
                bp_price = float(bp.get("entry_price", bp.get("current_price", 0)))
                lp_price = float(lp.get("entry_price", lp.get("current_price", 0)))
                if (
                    bp_price > 0
                    and lp_price > 0
                    and abs(bp_price - lp_price) / max(bp_price, lp_price) > 0.01
                ):
                    mismatches.append(
                        MismatchDetail(
                            MismatchType.PRICE_MISMATCH,
                            f"Price mismatch for {key}: broker={bp_price} local={lp_price}",
                            severity=2,
                        )
                    )

        broker_ord_by_id = self._index_orders(broker_orders)
        local_ord_by_id = self._index_orders(local_orders)

        all_order_ids = set(broker_ord_by_id) | set(local_ord_by_id)

        for oid in all_order_ids:
            bo = broker_ord_by_id.get(oid)
            lo = local_ord_by_id.get(oid)

            if bo is not None and lo is None:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.BROKER_ONLY_ORDER,
                        f"Broker has order {oid} not found locally",
                        severity=2,
                    )
                )
            elif lo is not None and bo is None:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.LOCAL_ONLY_ORDER,
                        f"Local has order {oid} not found on broker",
                        severity=2,
                    )
                )

        seen_pos_keys: set[str] = set()
        for pos in broker_positions:
            key = self._position_key(pos)
            if key in seen_pos_keys:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.DUPLICATE_BROKER_STATE,
                        f"Duplicate position key {key} from broker",
                        severity=2,
                    )
                )
            seen_pos_keys.add(key)

        seen_ord_ids: set[str] = set()
        for ord_ in broker_orders:
            oid = self._order_id(ord_)
            if oid in seen_ord_ids:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.DUPLICATE_BROKER_STATE,
                        f"Duplicate order id {oid} from broker",
                        severity=2,
                    )
                )
            seen_ord_ids.add(oid)

        if self._reconciled_at is not None:
            elapsed = (datetime.now(UTC) - self._reconciled_at).total_seconds()
            if elapsed > _STALE_THRESHOLD_SECONDS:
                mismatches.append(
                    MismatchDetail(
                        MismatchType.STALE_SNAPSHOT,
                        f"Last reconciliation was {elapsed:.0f}s ago "
                        f"(threshold {_STALE_THRESHOLD_SECONDS}s)",
                        severity=1,
                    )
                )

        reconciled_positions = matched_positions
        if not errors and not mismatches:
            self._startup_reconciled = True
            self._reconciled_at = datetime.now(UTC)
            self._consecutive_failures = 0
        elif mismatches:
            self._consecutive_failures += 1

        if errors:
            self._consecutive_failures += len(errors)

        return BrokerReconciliationResult(
            matched_positions=matched_positions,
            reconciled_positions=reconciled_positions,
            errors=errors,
            mismatches=mismatches,
            timestamp=datetime.now(UTC),
        )

    def _position_key(self, pos: dict) -> str:
        return str(pos.get("symbol", pos.get("market_id", pos.get("id", ""))))

    def _index_positions(self, positions: list[dict]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for p in positions:
            key = self._position_key(p)
            if key not in result:
                result[key] = p
        return result

    def _order_id(self, ord_: dict) -> str:
        return str(ord_.get("id", ord_.get("order_id", "")))

    def _index_orders(self, orders: list[dict]) -> dict[str, dict]:
        result: dict[str, dict] = {}
        for o in orders:
            oid = self._order_id(o)
            if oid not in result:
                result[oid] = o
        return result
