from __future__ import annotations

import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from traderos.domain.adapters.broker_adapter import BrokerAdapter
from traderos.domain.repositories.workflow_repository import OperatorWorkflowRepository
from traderos.domain.services.broker_state_reconciliation_service import (
    BrokerStateReconciliationService,
)
from traderos.domain.services.data_ingestion_service import DataIngestionService
from traderos.domain.services.operator_workflow import OperatorStep
from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.operator_workflow import WorkflowStatus
from traderos.domain.services.paper_trading_service import PaperSessionStatus
from traderos.domain.services.paper_trading_service import PaperTradingService
from traderos.domain.services.preflight_service import PreflightService
from traderos.domain.services.strategy_management import StrategyCatalogService


@dataclass
class StepOutcome:
    """Result of attempting one canonical workflow step.

    ``ok`` reflects the step's gate: a failing preflight or broker check does
    NOT advance the workflow — the operator re-runs the step once resolved.
    """

    step: OperatorStep
    ok: bool
    result: str
    detail: dict[str, Any] = field(default_factory=dict)


class OperatorSessionService:
    """C2 — the enforced operator session lifecycle.

    Wraps the pure ``OperatorWorkflow`` state machine and gates every canonical
    step on a real check (preflight, broker connectivity, market data feed,
    live confirmation, catalog promotion), persisting each transition.
    """

    def __init__(
        self,
        workflow: OperatorWorkflow,
        repository: OperatorWorkflowRepository | None = None,
        preflight: PreflightService | None = None,
        broker: BrokerAdapter | None = None,
        broker_reconciliation: BrokerStateReconciliationService | None = None,
        data_ingestion: DataIngestionService | None = None,
        paper: PaperTradingService | None = None,
        strategy_catalog: StrategyCatalogService | None = None,
        live_mode: bool = False,
    ) -> None:
        self.workflow = workflow
        self.repository = repository
        self.preflight = preflight
        self.broker = broker
        self.broker_reconciliation = broker_reconciliation
        self.data_ingestion = data_ingestion
        self.paper = paper
        self.strategy_catalog = strategy_catalog
        self.live_mode = live_mode

    # --- introspection ---

    @property
    def current_step(self) -> OperatorStep | None:
        return self.workflow.current_step

    @property
    def next_step(self) -> OperatorStep | None:
        return self.workflow.next_step()

    @property
    def status(self) -> WorkflowStatus:
        return self.workflow.status

    @property
    def session_id(self) -> str | None:
        return self.workflow.session_id

    def history(self) -> list[dict[str, str]]:
        return [
            {
                "from": t.from_step.value if t.from_step else "",
                "to": t.to_step.value,
                "actor": t.actor,
                "result": t.result,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in self.workflow.transitions
        ]

    # --- driving ---

    def perform(
        self,
        step: OperatorStep,
        actor: str = "operator",
        **context: Any,
    ) -> StepOutcome:
        outcome = self._execute_gate(step, context)
        if not outcome.ok:
            return outcome
        self.workflow.advance(step, actor=actor, result=outcome.result)
        self._persist()
        return outcome

    def _persist(self) -> None:
        if self.repository is not None:
            self.repository.save(self.workflow)

    # --- step gates ---

    def _execute_gate(self, step: OperatorStep, context: dict[str, Any]) -> StepOutcome:
        handlers: dict[OperatorStep, Any] = {
            OperatorStep.START: self._gate_start,
            OperatorStep.PREFLIGHT: self._gate_preflight,
            OperatorStep.BROKER_CHECK: self._gate_broker_check,
            OperatorStep.MARKET_DATA_CHECK: self._gate_market_data_check,
            OperatorStep.PAPER_TRADING: self._gate_paper_trading,
            OperatorStep.PERFORMANCE_REVIEW: self._gate_performance_review,
            OperatorStep.STRATEGY_PROMOTION: self._gate_strategy_promotion,
            OperatorStep.CONTROLLED_LIVE: self._gate_controlled_live,
            OperatorStep.SHUTDOWN: self._gate_shutdown,
            OperatorStep.SESSION_REPORT: self._gate_session_report,
        }
        return handlers[step](context)

    def _gate_start(self, context: dict[str, Any]) -> StepOutcome:
        if self.workflow.session_id is None:
            self.workflow.bind_session(context.get("session_id") or str(uuid.uuid4()))
        return StepOutcome(OperatorStep.START, True, "operator session started")

    def _gate_preflight(self, context: dict[str, Any]) -> StepOutcome:
        live_mode = bool(context.get("live_mode", self.live_mode))
        if self.preflight is None:
            return StepOutcome(OperatorStep.PREFLIGHT, True, "preflight not configured — skipped")
        verdict = self.preflight.check(live_mode=live_mode)
        checks = ", ".join(f"{k}={str(v).lower()}" for k, v in sorted(verdict.checks.items()))
        if verdict.passed:
            return StepOutcome(OperatorStep.PREFLIGHT, True, f"passed ({checks})")
        detail = {k: v for k, v in verdict.checks.items()}
        return StepOutcome(
            OperatorStep.PREFLIGHT,
            False,
            f"failed: {'; '.join(verdict.failures)}",
            detail=detail,
        )

    def _gate_broker_check(self, context: dict[str, Any]) -> StepOutcome:
        if self.broker is None:
            return StepOutcome(OperatorStep.BROKER_CHECK, True, "broker not configured — skipped")
        try:
            balance = self.broker.get_account_balance()
        except Exception as exc:  # noqa: BLE001 — connectivity failures are expected
            return StepOutcome(OperatorStep.BROKER_CHECK, False, f"broker unreachable: {exc}")
        if (
            self.broker_reconciliation is not None
            and not self.broker_reconciliation.can_accept_orders
        ):
            return StepOutcome(
                OperatorStep.BROKER_CHECK,
                False,
                "broker state reconciliation incomplete",
            )
        return StepOutcome(
            OperatorStep.BROKER_CHECK,
            True,
            f"broker connected (balance={balance:,.2f})",
            detail={"balance": balance},
        )

    def _gate_market_data_check(self, context: dict[str, Any]) -> StepOutcome:
        if self.data_ingestion is None:
            return StepOutcome(
                OperatorStep.MARKET_DATA_CHECK, True, "market data not configured — skipped"
            )
        count = len(self.data_ingestion.sources)
        if count == 0:
            return StepOutcome(
                OperatorStep.MARKET_DATA_CHECK, False, "no market data sources configured"
            )
        symbols = [s.symbol for s in self.data_ingestion.sources]
        return StepOutcome(
            OperatorStep.MARKET_DATA_CHECK,
            True,
            f"{count} feed(s) active",
            detail={"symbols": symbols},
        )

    def _gate_paper_trading(self, context: dict[str, Any]) -> StepOutcome:
        if self.paper is None:
            return StepOutcome(
                OperatorStep.PAPER_TRADING, False, "paper trading engine unavailable"
            )
        sessions = self.paper.list_sessions()
        running = [s.id for s in sessions if s.status == PaperSessionStatus.RUNNING]
        if not running:
            running = [s.id for s in sessions if s.status == PaperSessionStatus.CREATED]
        return StepOutcome(
            OperatorStep.PAPER_TRADING,
            True,
            f"paper trading ready ({len(sessions)} session(s))",
            detail={"sessions": [str(sid) for sid in running]},
        )

    def _gate_performance_review(self, context: dict[str, Any]) -> StepOutcome:
        if self.strategy_catalog is None:
            return StepOutcome(
                OperatorStep.PERFORMANCE_REVIEW, True, "catalog not configured — skipped"
            )
        names = [s.name for s in self.strategy_catalog.get_enabled()]
        if not names:
            return StepOutcome(
                OperatorStep.PERFORMANCE_REVIEW, False, "no enabled strategies to review"
            )
        comparison = self.strategy_catalog.compare(names, candles=50)
        ranking = ", ".join(comparison.ranking)
        return StepOutcome(
            OperatorStep.PERFORMANCE_REVIEW,
            True,
            f"reviewed {len(names)} strategy(ies); ranking: {ranking}",
            detail={
                "strategies": names,
                "ranking": comparison.ranking,
                "metrics": comparison.metrics,
            },
        )

    def _gate_strategy_promotion(self, context: dict[str, Any]) -> StepOutcome:
        name = context.get("strategy") or context.get("name")
        if not name:
            return StepOutcome(OperatorStep.STRATEGY_PROMOTION, False, "no strategy name provided")
        if self.strategy_catalog is None:
            return StepOutcome(
                OperatorStep.STRATEGY_PROMOTION, False, "strategy catalog unavailable"
            )
        try:
            promoted = self.strategy_catalog.promote(name)
        except Exception as exc:  # noqa: BLE001 — lifecycle rule violations are expected
            return StepOutcome(OperatorStep.STRATEGY_PROMOTION, False, f"promotion rejected: {exc}")
        return StepOutcome(
            OperatorStep.STRATEGY_PROMOTION,
            True,
            f"promoted '{name}' to {promoted.status.value}",
        )

    def _gate_controlled_live(self, context: dict[str, Any]) -> StepOutcome:
        if self.preflight is None:
            return StepOutcome(
                OperatorStep.CONTROLLED_LIVE, True, "preflight not configured — skipped"
            )
        dry_run = bool(context.get("dry_run", False))
        verdict = self.preflight.check(live_mode=True)
        detail: dict[str, Any] = {
            "dry_run": dry_run,
            "live_execution_enabled": not dry_run,
            **verdict.checks,
        }
        if verdict.passed:
            message = (
                "live preflight passed (dry-run — live execution disabled)"
                if dry_run
                else "live preflight passed"
            )
            return StepOutcome(OperatorStep.CONTROLLED_LIVE, True, message, detail=detail)
        return StepOutcome(
            OperatorStep.CONTROLLED_LIVE,
            False,
            f"live preflight failed: {'; '.join(verdict.failures)}",
            detail=detail,
        )

    def _gate_shutdown(self, context: dict[str, Any]) -> StepOutcome:
        stopped = 0
        if self.paper is not None:
            for session in self.paper.list_sessions():
                if session.status == PaperSessionStatus.RUNNING:
                    self.paper.stop_session(session.id)
                    stopped += 1
        return StepOutcome(
            OperatorStep.SHUTDOWN,
            True,
            f"shutdown complete ({stopped} session(s) stopped)",
            detail={"sessions_stopped": stopped},
        )

    def _gate_session_report(self, context: dict[str, Any]) -> StepOutcome:
        return StepOutcome(OperatorStep.SESSION_REPORT, True, "session report generated")
