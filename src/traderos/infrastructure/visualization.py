from __future__ import annotations

from typing import NamedTuple


class ChartPoint(NamedTuple):
    label: str
    value: float


class ChartSeries(NamedTuple):
    name: str
    points: list[ChartPoint]


class BarChart(NamedTuple):
    title: str
    series: list[ChartSeries]


class LineChart(NamedTuple):
    title: str
    series: list[ChartSeries]
    x_label: str = ""
    y_label: str = ""


class VisualizationService:
    def equity_curve(self, curve: list[tuple[str, float]]) -> LineChart:
        points = [ChartPoint(label=t, value=v) for t, v in curve]
        return LineChart(
            title="Equity Curve",
            series=[ChartSeries(name="Equity", points=points)],
            x_label="Time",
            y_label="Value",
        )

    def returns_distribution(self, returns: list[float]) -> BarChart:
        buckets: dict[str, int] = {
            "< -5%": 0, "-5% to -2%": 0, "-2% to 0%": 0,
            "0% to 2%": 0, "2% to 5%": 0, "> 5%": 0,
        }
        for r in returns:
            pct = r * 100
            if pct < -5:
                buckets["< -5%"] += 1
            elif pct < -2:
                buckets["-5% to -2%"] += 1
            elif pct < 0:
                buckets["-2% to 0%"] += 1
            elif pct < 2:
                buckets["0% to 2%"] += 1
            elif pct < 5:
                buckets["2% to 5%"] += 1
            else:
                buckets["> 5%"] += 1
        points = [ChartPoint(label=k, value=v) for k, v in buckets.items()]
        return BarChart(
            title="Returns Distribution",
            series=[ChartSeries(name="Frequency", points=points)],
        )

    def drawdown_chart(self, equity_curve: list[float]) -> LineChart:
        peak = equity_curve[0] if equity_curve else 0.0
        dd_points: list[ChartPoint] = []
        for i, v in enumerate(equity_curve):
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0.0
            dd_points.append(ChartPoint(label=str(i), value=round(dd, 2)))
        return LineChart(
            title="Drawdown",
            series=[ChartSeries(name="Drawdown %", points=dd_points)],
            x_label="Period",
            y_label="Drawdown %",
        )

    def performance_summary(
        self, metrics: dict[str, float]
    ) -> BarChart:
        points = [
            ChartPoint(label=k.replace("_", " ").title(), value=v)
            for k, v in metrics.items()
        ]
        return BarChart(
            title="Performance Summary",
            series=[ChartSeries(name="Metrics", points=points)],
        )
