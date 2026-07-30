from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from traderos.application.daemon_controller import DaemonController
from traderos.application.models import TradingMode
from traderos.domain.services.broker_state_reconciliation_service import BrokerReconciliationResult
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.broker_state_reconciliation_service import MismatchDetail
from traderos.domain.services.broker_state_reconciliation_service import MismatchType


def _make_controller(
    health: MagicMock | None = None,
    audit: MagicMock | None = None,
    metrics: MagicMock | None = None,
    notifications: MagicMock | None = None,
    kill_switch: MagicMock | None = None,
) -> DaemonController:
    return DaemonController(
        mode=TradingMode.PAPER,
        cycle_executor=MagicMock(),
        event_bus=MagicMock(),
        health=health or MagicMock(),
        audit=audit or MagicMock(),
        metrics=metrics or MagicMock(),
        notifications=notifications or MagicMock(),
        run_manifest=MagicMock(),
        kill_switch=kill_switch or MagicMock(),
    )


# Mismatch types with severity >= 2 (should trip kill switch, block orders)
SEVERITY_2_MISMATCHES = [
    MismatchType.BROKER_ONLY_POSITION,
    MismatchType.LOCAL_ONLY_POSITION,
    MismatchType.QUANTITY_MISMATCH,
    MismatchType.PRICE_MISMATCH,
    MismatchType.STALE_SNAPSHOT,
    MismatchType.DUPLICATE_BROKER_STATE,
]


class TestReconciliationEffectsMatrix:
    """60-assertion matrix: 10 mismatch types × 6 effects.

    Each mismatch type is tested for:
    E1 — Detection: result returned False (orders blocked)
    E2 — Health: report_unhealthy called with broker_reconciliation component
    E3 — Kill-switch: record_failure called when severity >= 2
    E4 — Audit: record called with reconciliation.mismatch action
    E5 — Metrics: counter called with mismatch type (severity >= 2)
    E6 — Notification: warning called with mismatch description
    """

    @pytest.mark.parametrize(
        "mismatch_type",
        [
            MismatchType.BROKER_ONLY_POSITION,
            MismatchType.LOCAL_ONLY_POSITION,
            MismatchType.QUANTITY_MISMATCH,
            MismatchType.PRICE_MISMATCH,
            MismatchType.BROKER_ONLY_ORDER,
            MismatchType.LOCAL_ONLY_ORDER,
            MismatchType.STALE_SNAPSHOT,
            MismatchType.DUPLICATE_BROKER_STATE,
            MismatchType.BROKER_FAILURE,
            MismatchType.UNKNOWN_STATE,
        ],
    )
    def test_reconciliation_effects_matrix(self, mismatch_type: MismatchType) -> None:
        health = MagicMock()
        audit = MagicMock()
        metrics = MagicMock()
        notifications = MagicMock()
        kill_switch = MagicMock()
        ctrl = _make_controller(health, audit, metrics, notifications, kill_switch)

        severity = 2 if mismatch_type in SEVERITY_2_MISMATCHES else 1
        detail = MismatchDetail(
            mismatch_type=mismatch_type,
            description=f"Test {mismatch_type.value} mismatch",
            severity=severity,
        )

        if mismatch_type == MismatchType.BROKER_FAILURE:
            result = BrokerReconciliationResult(
                matched_positions=0,
                reconciled_positions=0,
                mismatches=[],
                errors=["Broker unreachable"],
            )
        elif mismatch_type == MismatchType.UNKNOWN_STATE:
            result = BrokerReconciliationResult(
                matched_positions=0,
                reconciled_positions=0,
                mismatches=[detail],
                errors=[],
            )
        else:
            result = BrokerReconciliationResult(
                matched_positions=0,
                reconciled_positions=0,
                mismatches=[detail],
                errors=[],
            )

        returned = ctrl._handle_reconciliation_result(result)

        # E1 — Detection: result returns False when mismatches/errors
        assert returned is False, (
            f"E1 failed for {mismatch_type.value}: "
            f"_handle_reconciliation_result should return False"
        )

        # E2 — Health: report_unhealthy called for broker_reconciliation
        if mismatch_type == MismatchType.BROKER_FAILURE:
            health.report_unhealthy.assert_called_with(
                "broker_reconciliation", "Broker unreachable"
            )
        else:
            health_calls = health.report_unhealthy.call_args_list
            matching_calls = [
                c
                for c in health_calls
                if c[0][0] == "broker_reconciliation" and mismatch_type.value in c[0][1]
            ]
            assert matching_calls, (
                f"E2 failed for {mismatch_type.value}: "
                f"report_unhealthy(broker_reconciliation, {mismatch_type.value}) not called. "
                f"All calls: {health_calls}"
            )

        # E3 — Kill-switch: record_failure called when severity >= 2
        # BROKER_FAILURE goes through errors path, which always calls record_failure
        if mismatch_type == MismatchType.BROKER_FAILURE:
            assert kill_switch.record_failure.called, (
                f"E3 failed for {mismatch_type.value}: "
                f"kill_switch.record_failure should be called for errors"
            )
        elif severity >= 2:
            assert kill_switch.record_failure.called, (
                f"E3 failed for {mismatch_type.value}: "
                f"kill_switch.record_failure should be called for severity={severity}"
            )
        else:
            assert not kill_switch.record_failure.called, (
                f"E3 failed for {mismatch_type.value}: "
                f"kill_switch.record_failure should NOT be called for severity={severity}"
            )

        # E4 — Audit: record called with reconciliation.mismatch
        if mismatch_type == MismatchType.BROKER_FAILURE:
            audit.record.assert_called_with(
                "reconciliation.error", "system", "broker_reconciliation", "Broker unreachable"
            )
        else:
            audit_calls = audit.record.call_args_list
            matching_audit = [c for c in audit_calls if c[0][0] == "reconciliation.mismatch"]
            assert matching_audit, (
                f"E4 failed for {mismatch_type.value}: "
                f"audit.record not called with reconciliation.mismatch. "
                f"All calls: {audit_calls}"
            )

        # E5 — Metrics: counter called
        # BROKER_FAILURE goes through errors path: reconciliation.errors counter
        if mismatch_type == MismatchType.BROKER_FAILURE:
            error_counter_calls = [
                c for c in metrics.counter.call_args_list if c[0][0] == "reconciliation.errors"
            ]
            assert error_counter_calls, (
                f"E5 failed for {mismatch_type.value}: "
                f"metrics.counter not called with reconciliation.errors. "
                f"All calls: {metrics.counter.call_args_list}"
            )
        else:
            # For severity >= 2 mismatches, per-type counter
            if severity >= 2:
                type_counter_calls = [
                    c
                    for c in metrics.counter.call_args_list
                    if c[0][0] == f"reconciliation.{mismatch_type.value}"
                ]
                assert type_counter_calls, (
                    f"E5 failed for {mismatch_type.value}: "
                    f"metrics.counter not called with reconciliation.{mismatch_type.value}. "
                    f"All calls: {metrics.counter.call_args_list}"
                )
            # Total mismatches counter always called for mismatches
            total_counter_calls = [
                c for c in metrics.counter.call_args_list if c[0][0] == "reconciliation.mismatches"
            ]
            assert total_counter_calls, (
                f"E5 failed for {mismatch_type.value}: "
                f"metrics.counter not called with reconciliation.mismatches. "
                f"All calls: {metrics.counter.call_args_list}"
            )

        # E6 — Notification: warning called with mismatch description
        if mismatch_type == MismatchType.BROKER_FAILURE:
            notifications.warning.assert_called_with("Reconciliation", "Broker unreachable")
        else:
            notif_calls = notifications.warning.call_args_list
            matching_notif = [
                c
                for c in notif_calls
                if c[0][0] == "Reconciliation Mismatch" and mismatch_type.value in c[0][1]
            ]
            assert matching_notif, (
                f"E6 failed for {mismatch_type.value}: "
                f"notifications.warning not called with Reconciliation Mismatch "
                f"and {mismatch_type.value}. All calls: {notif_calls}"
            )

    def test_healthy_not_overwritten_on_mismatch(self) -> None:
        """Regression: health.report_healthy must NOT be called when mismatches exist."""
        health = MagicMock()
        ctrl = _make_controller(health=health)
        detail = MismatchDetail(MismatchType.QUANTITY_MISMATCH, "qty mismatch", severity=2)
        result = BrokerReconciliationResult(
            matched_positions=0,
            reconciled_positions=0,
            mismatches=[detail],
            errors=[],
        )
        ctrl._handle_reconciliation_result(result)

        healthy_calls = [
            c for c in health.report_healthy.call_args_list if c[0][0] == "broker_reconciliation"
        ]
        assert not healthy_calls, (
            f"report_healthy should NOT be called when mismatches exist. " f"Calls: {healthy_calls}"
        )

    def test_healthy_reported_when_no_mismatches(self) -> None:
        """When no mismatches, report_healthy should be called."""
        health = MagicMock()
        ctrl = _make_controller(health=health)
        result = BrokerReconciliationResult(
            matched_positions=1,
            reconciled_positions=0,
            mismatches=[],
            errors=[],
        )
        returned = ctrl._handle_reconciliation_result(result)
        assert returned is True
        health.report_healthy.assert_called_with(
            "broker_reconciliation",
            "matched=1 reconciled=0",
        )

    def test_stale_snapshot_now_trips_kill_switch(self) -> None:
        """Regression: STALE_SNAPSHOT severity changed from 1 to 2."""
        kill_switch = MagicMock()
        _make_controller(kill_switch=kill_switch)
        svc = BrokerStateReconciliationService(broker=MagicMock())
        svc._startup_reconciled = True
        import datetime

        svc._reconciled_at = datetime.datetime.now(datetime.UTC).replace(year=2020, month=1, day=1)
        result = svc.reconcile()
        assert result.has_mismatches
        stale = [m for m in result.mismatches if m.mismatch_type == MismatchType.STALE_SNAPSHOT]
        assert stale, "Should have STALE_SNAPSHOT mismatch"
        assert stale[0].severity >= 2, "STALE_SNAPSHOT should have severity >= 2"
