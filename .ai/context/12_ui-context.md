# 12 — UI Context

## Purpose
Future dashboard philosophy, visual language, and design system. Defines how TraderOS presents information to users. Not implemented yet — serves as design contract for future UI work.

## Authority Level
**Directional** — design decisions are aspirational until WP-058.

## Consumers
AI agents designing UI, frontend engineers, product designers.

## Dependencies
- Constitution §5 (Target Architecture — Interface Layer)
- `.ai/context/01_architecture.md` — interface layer boundaries

## Source Documents
- Constitution §5 Interface Layer description
- Master Execution Programme §4 (Capability Map)

## Update Rules
- Finalized when WP-058 (Web Dashboard) begins
- ADR required for design system changes

---

## Philosophy

**Information, not decoration**. Every pixel serves a purpose. The dashboard is a tool for decision-making, not a beauty contest. Data density is a feature.

## Visual Language

- **Monochromatic base** with semantic color for signals (green=go, red=stop, yellow=caution)
- **Dark mode default** (reduces eye strain for extended monitoring)
- **Minimal chrome**: no borders where alignment suffices
- **Data-ink ratio**: maximize data, minimize non-data ink (Tufte principle)
- **Typography**: system font stack (performance, no external loads)

## Charts

| Chart Type | Purpose | Library | Priority |
|-----------|---------|---------|----------|
| OHLCV candlestick | Price action | Matplotlib → Plotly | P0 |
| Line chart | Equity curves, indicators | Matplotlib | P0 |
| Heatmap | Correlation matrix | Seaborn | P0 |
| Bar chart | Strategy comparison | Matplotlib | P1 |
| Scatter plot | Risk/return scatter | Matplotlib | P1 |
| Waterfall | Portfolio PnL decomposition | Custom | P2 |

## Navigation

```
Dashboard Home
├── Market Overview    (real-time status of all tracked symbols)
├── Research Lab       (knowledge graph browser)
│   ├── Observations
│   ├── Hypotheses
│   ├── Experiments
│   └── Lessons
├── Strategy Vault     (strategy registry & backtest history)
├── Risk Center        (current exposure, limits, kill switch status)
├── Portfolio          (positions, PnL, allocation)
└── Settings           (configuration, data sources, API keys)
```

## Information Hierarchy

1. **Alerts / Exceptions** (red, top) — kill switch, exposure breach, data gap
2. **Active State** (current) — positions, signals, running backtests
3. **Recent History** (last N) — recent trades, last N analyses
4. **Trends** (longer view) — performance curves, regime changes
5. **Raw Data** (drill-down) — OHLCV tables, logs, raw exports

## Accessibility

- All charts must have text alternatives (data tables alongside)
- Color is NOT the only differentiator (patterns, labels, annotations)
- Keyboard navigable (Tab/Shift+Tab through interactive elements)
- Screen reader compatible (ARIA labels on web components)
- Minimum contrast ratio: 4.5:1 for text, 3:1 for large text

## Design System (Future)

```
Colors:
  --bg-primary: #0d1117      (background)
  --bg-secondary: #161b22    (card backgrounds)
  --text-primary: #c9d1d9    (body text)
  --text-secondary: #8b949e  (labels, metadata)
  --accent-green: #3fb950    (buy, up, long)
  --accent-red: #f85149      (sell, down, short)
  --accent-yellow: #d29922   (warning)
  --accent-blue: #58a6ff     (info, link)

Spacing: 4px base unit (4, 8, 12, 16, 20, 24, 32, 48, 64)
Typography: system-ui, -apple-system, sans-serif
           Regular: 400, Medium: 500, Bold: 600
           Body: 14px, Heading: 20px, Title: 28px
```

## Implementation Priority

| Phase | What | When |
|-------|------|------|
| 1 | Matplotlib charts (current) | Now |
| 2 | Interactive CLI dashboard (tabulate tables) | Current |
| 3 | Plotly web dashboard | Post-MVP |
| 4 | Real-time WebSocket updates | v1.1 |
| 5 | Native mobile companion | v2 |

## References
- [C:5] Target System Architecture — Interface Layer diagram
- Master Execution Programme WP-055 — Unified CLI Framework
- Master Execution Programme WP-058 — Web Dashboard
- Edward Tufte, "The Visual Display of Quantitative Information" — guiding philosophy
