import pandas as pd


class LiquidityZoneMapper:
    def __init__(self, threshold: float = 0.002):
        self.threshold = threshold

    def map_zones(self, df: pd.DataFrame) -> list[dict]:
        """Identify support and resistance zones by clustering swing points."""
        highs = df[df["swing_high"].notna()]["swing_high"].tolist()
        lows = df[df["swing_low"].notna()]["swing_low"].tolist()

        zones = []

        # Simple clustering logic for Resistance
        for price in set(highs):
            count = sum(1 for h in highs if abs(h - price) / price < self.threshold)
            if count >= 2:
                zones.append({"price_level": price, "zone_type": "Resistance", "strength": count})

        # Simple clustering logic for Support
        for price in set(lows):
            count = sum(1 for low in lows if abs(low - price) / price < self.threshold)
            if count >= 2:
                zones.append({"price_level": price, "zone_type": "Support", "strength": count})

        # Remove duplicates/very close zones
        unique_zones = self._filter_zones(zones)
        return unique_zones

    def _filter_zones(self, zones: list[dict]) -> list[dict]:
        if not zones:
            return []

        sorted_zones = sorted(zones, key=lambda x: x["price_level"])
        filtered = []
        if sorted_zones:
            current = sorted_zones[0]
            for next_zone in sorted_zones[1:]:
                if (next_zone["price_level"] - current["price_level"]) / current[
                    "price_level"
                ] < self.threshold:
                    # Keep the one with more strength
                    if next_zone["strength"] > current["strength"]:
                        current = next_zone
                else:
                    filtered.append(current)
                    current = next_zone
            filtered.append(current)
        return filtered
