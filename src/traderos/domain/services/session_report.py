from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from typing import Any

from traderos.domain.services.operator_workflow import OperatorWorkflow
from traderos.domain.services.portfolio_service import PortfolioService
from traderos.domain.services.risk_service import RiskService
from traderos.domain.services.strategy_management import StrategyCatalogService


@dataclass
class SessionReport:
    """C4 — immutable snapshot of one operator session for the dashboard."""

    session_id: str | None
    generated_at: datetime
    workflow_status: str
    current_step: str | None
    steps: list[dict[str, str]]
    portfolio: dict[str, float]
    positions: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    strategies: list[dict[str, Any]]
    promoted_strategy: str | None
    risk: dict[str, Any]
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "generated_at": self.generated_at.isoformat(),
            "workflow_status": self.workflow_status,
            "current_step": self.current_step,
            "steps": self.steps,
            "portfolio": self.portfolio,
            "positions": self.positions,
            "trades": self.trades,
            "strategies": self.strategies,
            "promoted_strategy": self.promoted_strategy,
            "risk": self.risk,
            "duration_seconds": self.duration_seconds,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_markdown(self) -> str:
        lines = [
            f"# Session Report — {self.session_id or 'n/a'}",
            f"Generated: {self.generated_at.isoformat()}",
            f"Workflow: {self.workflow_status} (current step: {self.current_step or '—'})",
            "",
            "## Portfolio",
            f"- Equity: {self.portfolio.get('total_equity', 0.0):,.2f}",
            f"- Cash: {self.portfolio.get('cash', 0.0):,.2f}",
            f"- Open positions: {len(self.positions)}",
            f"- Total PnL: {self.portfolio.get('total_pnl', 0.0):,.2f}",
            "",
            "## Risk",
            f"- Kill switch engaged: {self.risk.get('engaged', False)}",
            "",
            "## Strategies",
        ]
        for s in self.strategies:
            marker = " (promoted)" if s["name"] == self.promoted_strategy else ""
            lines.append(f"- {s['name']} [{s['status']}]{marker}")
        lines.append("")
        lines.append(f"## Steps taken ({len(self.steps)})")
        for step in self.steps:
            lines.append(
                f"- {step['to']}: {step['result']} (by {step['actor']} at {step['timestamp']})"
            )
        return "\n".join(lines)


class SessionReportService:
    """Builds an operator session report from live runtime state."""

    def __init__(
        self,
        workflow: OperatorWorkflow,
        portfolio: PortfolioService | None = None,
        risk: RiskService | None = None,
        strategy_catalog: StrategyCatalogService | None = None,
        cash: float = 0.0,
    ) -> None:
        self.workflow = workflow
        self.portfolio = portfolio
        self.risk = risk
        self.strategy_catalog = strategy_catalog
        self.cash = cash

    def generate(self) -> SessionReport:
        now = datetime.now(UTC)
        steps: list[dict[str, str]] = [
            {
                "from": t.from_step.value if t.from_step else "",
                "to": t.to_step.value,
                "actor": t.actor,
                "result": t.result,
                "timestamp": t.timestamp.isoformat(),
            }
            for t in self.workflow.transitions
        ]

        portfolio: dict[str, float] = {}
        if self.portfolio is not None:
            summary = self.portfolio.get_summary(self.cash)
            portfolio = {
                "total_equity": summary.total_equity,
                "cash": summary.cash,
                "positions_value": summary.positions_value,
                "total_pnl": summary.total_pnl,
                "position_count": summary.position_count,
            }

        positions: list[dict[str, Any]] = []
        if self.portfolio is not None:
            positions = [
                {
                    "id": str(p.id),
                    "market_id": str(p.market_id),
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "pnl": p.pnl,
                    "realized_pnl": p.realized_pnl,
                }
                for p in self.portfolio.position_repo.list()
            ]

        trades: list[dict[str, Any]] = []
        if self.portfolio is not None:
            trades = [
                {
                    "id": str(t.id),
                    "market_id": str(t.market_id),
                    "side": t.side.value,
                    "quantity": t.quantity,
                    "price": t.price,
                    "status": t.status.value,
                    "created_at": t.created_at.isoformat(),
                }
                for t in self.portfolio.trade_repo.list()
            ]

        strategies: list[dict[str, Any]] = []
        promoted: str | None = None
        if self.strategy_catalog is not None:
            for s in self.strategy_catalog.list():
                entry = {
                    "name": s.name,
                    "template": s.template,
                    "status": s.status.value,
                    "params": s.params,
                }
                strategies.append(entry)
                if s.status.value == "promoted":
                    promoted = s.name

        risk: dict[str, Any] = {"engaged": False}
        if self.risk is not None:
            verdict = self.risk.kill_switch.can_trade()
            risk = {
                "engaged": not verdict.allowed,
                "reason": verdict.reason,
                "circuit_open": self.risk.kill_switch.circuit_open,
                "consecutive_failures": self.risk.kill_switch.consecutive_failures,
            }

        duration = None
        if self.workflow.started_at is not None and self.workflow.completed_at is not None:
            duration = (self.workflow.completed_at - self.workflow.started_at).total_seconds()

        return SessionReport(
            session_id=self.workflow.session_id,
            generated_at=now,
            workflow_status=self.workflow.status.value,
            current_step=self.workflow.current_step.value if self.workflow.current_step else None,
            steps=steps,
            portfolio=portfolio,
            positions=positions,
            trades=trades,
            strategies=strategies,
            promoted_strategy=promoted,
            risk=risk,
            duration_seconds=duration,
        )
