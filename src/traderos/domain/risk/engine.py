from traderos.infrastructure.database.db_manager import DatabaseManager


class RiskEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.limits = self._load_limits()

    def _load_limits(self) -> dict:
        cursor = self.db.conn.cursor()
        cursor.execute(
            "SELECT max_drawdown, max_position_size, max_correlation "
            "FROM risk_limits WHERE is_active = 1 LIMIT 1"
        )
        row = cursor.fetchone()
        if row:
            return {"max_drawdown": row[0], "max_position_size": row[1], "max_correlation": row[2]}
        return {"max_drawdown": -0.15, "max_position_size": 0.10, "max_correlation": 0.85}

    def calculate_position_size(
        self, capital: float, volatility: float, risk_factor: float = 0.01
    ) -> float:
        """Volatility-based position sizing (Kelly-lite)."""
        if volatility == 0:
            return capital * self.limits["max_position_size"]

        # Size = (Capital * Risk%) / Volatility
        size = (capital * risk_factor) / volatility
        return min(size, capital * self.limits["max_position_size"])

    def check_kill_switch(self, current_drawdown: float, portfolio_correlation: float) -> bool:
        """Check if any safety limits are breached."""
        return (
            current_drawdown < self.limits["max_drawdown"]
            or portfolio_correlation > self.limits["max_correlation"]
        )

    def validate_exposure(
        self, current_exposure: float, new_position_size: float, total_capital: float
    ) -> bool:
        """Ensure new position doesn't exceed portfolio limits."""
        total_new_exposure = (current_exposure + new_position_size) / total_capital
        return total_new_exposure <= 0.50  # Hard limit: 50% total exposure
