# Market Intelligence Platform

## Project Overview

The Market Intelligence Platform is a lightweight yet scalable algorithmic trading research system designed to empower traders in Forex and Crypto markets. This MVP focuses on providing deep market understanding, detecting market regimes, monitoring volatility, analyzing correlations, journaling insights, and exploring strategy ideas to improve decision-making. It is built with a strong emphasis on clean architecture, modularity, speed of iteration, extensibility, observability, data integrity, and simplicity over complexity.

**Mission:** To build a market intelligence and research system that helps traders understand markets deeply before risking capital.

## Architecture

The platform adopts a **modular monorepo** architectural style, separating concerns into distinct layers and modules to ensure maintainability and extensibility. The initial MVP focuses on four core layers:

1.  **Data Engine:** Responsible for fetching, storing, and managing historical market data for Forex and Crypto assets.
2.  **Analysis Engine:** Processes raw market data to compute various technical indicators, market features, and detect market regimes.
3.  **Market Intelligence Layer:** Utilizes the processed data to identify correlations, provide a journaling system for insights, and generate actionable market insights.
4.  **Liquidity Mapping Engine:** Identifies key liquidity zones, market structure events like swing highs/lows, support/resistance, consolidation, breakouts, and liquidity sweeps, along with session-based behavior analysis.

### Core Components:

-   **`configs/`**: Manages application settings and configurations.
-   **`database/`**: Handles SQLite database interactions and schema management.
-   **`data_pipeline/`**: Contains data collectors for various assets and the pipeline orchestrator.
-   **`analysis_engine/`**: Implements market indicators and regime detection algorithms.
-   **`correlation_engine/`**: Computes and analyzes correlations between assets.
-   **`journal_engine/`**: Provides a system for logging trader observations and insights.
-   **`liquidity_engine/`**: Contains modules for swing detection, liquidity zone mapping, sweep detection, breakout detection, and session analysis.
-   **`visualization/`**: Utilities for generating charts and visual representations of market data and analysis.
-   **`scripts/`**: Placeholder for utility scripts.
-   **`tests/`**: Contains unit and integration tests.
-   **`main.py`**: The main entry point for the application.

## Roadmap

This project is planned in phases, ensuring a structured and incremental development process. The current MVP covers the initial phases, laying a solid foundation for future enhancements.

| Phase | Description |
| :---- | :---------- |
| **Phase 1** | Data Infrastructure: Establish robust data collection, storage, and management systems. |
| **Phase 2** | Market Analysis: Implement core analytical capabilities for market features and regime detection. |
| **Phase 3** | Correlation Engine: Develop the engine for computing and visualizing inter-asset correlations. |
| **Phase 4** | Liquidity Mapping: Integrate tools for understanding market liquidity dynamics. |


## Phase 0: Stabilization & Persistence

The platform has been upgraded to a **Persistent Learning System**. 

### Key Foundation Upgrades:
- **Persistence Layer:** All analysis (Regimes, Correlations, Liquidity) is now stored in SQLite. No more print-only insights.
- **Real Data:** Integrated **Binance** (via ccxt) and **yfinance** for real market feeds.
- **Knowledge Graph:** Implemented the `Observation -> Hypothesis -> Test -> Result -> Lesson` (O-H-T-R-L) workflow.
- **Research CLI:** A dedicated tool for managing your trading research and compounding knowledge.

## Research Workflow CLI

Manage your research directly from the terminal:

```bash
# 1. Log an observation
python3.11 research_cli.py obs BTC/USDT "Strong rejection at 70,000 psychological level."

# 2. Create a hypothesis from an observation (using ID from step 1)
python3.11 research_cli.py hyp 1 "Rejections at 70k lead to mean reversion to 20-day SMA."

# 3. List recent observations
python3.11 research_cli.py list

# 4. Trace a full research chain (after recording tests/results/lessons via API)
python3.11 research_cli.py trace 1
```

## Setup Instructions

To get started with the Market Intelligence Platform, follow these steps:

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/market-intelligence-platform.git
    cd market-intelligence-platform
    ```

2.  **Create a virtual environment (recommended):**
    ```bash
    python3.11 -m venv venv
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Environment Variables:**
    Create a `.env` file in the root directory based on `.env.example`. This will be used for sensitive information like API keys (though not strictly necessary for the current mock data implementation).
    ```ini
    # .env example
    # API_KEY_FOREX=your_forex_api_key
    # API_KEY_CRYPTO=your_crypto_api_key
    ```

5.  **Run the platform:**
    ```bash
    python3.11 main.py
    ```
    This will initiate data collection (using mock data for now), perform analysis, generate market intelligence, and map liquidity zones, producing insights and visualizations.

## Philosophy

The Market Intelligence Platform is envisioned as a blend of a **Bloomberg Terminal**, **TradingView**, and a **Personal Quant Lab**. It aims to be:

-   **Lightweight:** Focused on essential features without unnecessary bloat.
-   **Educational:** Designed to foster a deeper understanding of market dynamics.
-   **Research-First:** Prioritizing analysis and insight generation over immediate execution.
-   **Modular:** Easy to extend and adapt to new research needs.
-   **Developer-Friendly:** Clean codebase with clear documentation and type hints.

Our core belief is that profound market understanding is the precursor to successful trading. This platform provides the tools to achieve that understanding.

## Future Plans

While the current MVP focuses on data collection, analysis, and basic intelligence, the architecture is designed to accommodate future advanced features. These include, but are not limited to:

-   Integration with real-time data APIs for live market feeds.
-   Advanced machine learning models for predictive analytics and pattern recognition.
-   Sophisticated backtesting and simulation environments.
-   Autonomous trading agents (with careful risk management).
-   Cloud deployment options for scalability and accessibility.
-   User authentication and multi-user support.

These features will be introduced in later phases, ensuring that the platform remains robust and stable throughout its evolution.

## Market Structure Features

Layer 4 introduces advanced market structure analysis capabilities:

-   **Swing Point Detection:** Identifies recent swing highs and lows, crucial for understanding market turning points.
-   **Support & Resistance Zones:** Detects areas where price has repeatedly reacted, indicating potential supply and demand imbalances.
-   **Liquidity Sweeps:** Pinpoints instances where price briefly moves beyond a significant high or low before reversing, often signaling liquidity grabs by larger market participants.
-   **Consolidation Detection:** Identifies periods of low volatility and tight price ranges, suggesting accumulation or distribution phases.
-   **Breakout Detection:** Recognizes when price exits a consolidation range with increased volatility and directional movement.
-   **Session Analysis:** Tracks market behavior during key trading sessions (London, New York, Asian) to understand differences in volatility, breakout frequency, and liquidity sweeps.

## Screenshots (Placeholders)

*(Insert screenshots of price charts, volatility charts, correlation heatmaps, journal entries, and liquidity maps here once generated.)*

## Contributor Notes

We welcome contributions from the community! If you're interested in contributing, please review the existing codebase, adhere to the engineering rules (production-quality code, type hints, clear documentation, separation of concerns), and submit pull requests. For major changes, please open an issue first to discuss what you would like to change.

**Engineering Rules:**

-   Write production-quality code.
-   Use type hints extensively.
-   Document functions clearly.
-   Separate concerns properly; avoid monolithic files.
-   Keep functions small and focused.
-   Utilize configuration files for settings.
-   Manage sensitive data with environment variables.
-   Implement comprehensive logging.
-   Include robust error handling.
-   Maintain an extensible architecture.

Thank you for being a part of this journey to build a powerful market intelligence tool!
