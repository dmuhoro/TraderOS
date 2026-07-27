from __future__ import annotations

import pytest

from traderos.infrastructure.visualization import VisualizationService


class TestVisualizationService:
    def test_equity_curve(self) -> None:
        svc = VisualizationService()
        chart = svc.equity_curve([("t1", 100.0), ("t2", 110.0)])
        assert chart.title == "Equity Curve"
        assert len(chart.series) == 1
        assert len(chart.series[0].points) == 2

    def test_returns_distribution(self) -> None:
        svc = VisualizationService()
        chart = svc.returns_distribution([-0.06, -0.03, -0.01, 0.01, 0.03, 0.06])
        assert chart.title == "Returns Distribution"
        assert len(chart.series[0].points) == 6

    def test_returns_distribution_empty(self) -> None:
        svc = VisualizationService()
        chart = svc.returns_distribution([])
        assert all(p.value == 0 for p in chart.series[0].points)

    def test_drawdown_chart(self) -> None:
        svc = VisualizationService()
        chart = svc.drawdown_chart([100.0, 110.0, 105.0, 95.0])
        assert chart.title == "Drawdown"
        assert len(chart.series[0].points) == 4

    def test_drawdown_peak_update(self) -> None:
        svc = VisualizationService()
        chart = svc.drawdown_chart([100.0, 120.0, 110.0, 130.0])
        points = chart.series[0].points
        assert points[0].value == 0.0
        assert points[2].value == pytest.approx(8.33, rel=0.1)

    def test_performance_summary(self) -> None:
        svc = VisualizationService()
        chart = svc.performance_summary({"total_return": 0.15, "sharpe": 1.2})
        assert chart.title == "Performance Summary"
        assert len(chart.series[0].points) == 2
