from __future__ import annotations

from traderos.domain.entities import Indicator
from traderos.domain.entities import LiquidityZone
from traderos.domain.entities import ZoneType


class LiquidityZoneService:
    @staticmethod
    def map_zones_from_swings(
        swing_highs: list[Indicator],
        swing_lows: list[Indicator],
        threshold: float = 0.002,
    ) -> list[LiquidityZone]:
        zones: list[LiquidityZone] = []

        high_prices = [ind.value for ind in swing_highs]
        seen_highs: set[float] = set()
        for price in high_prices:
            if price in seen_highs:
                continue
            seen_highs.add(price)
            count = sum(1 for h in high_prices if abs(h - price) / max(price, 0.001) < threshold)
            if count >= 2:
                ts = max(
                    ind.timestamp
                    for ind in swing_highs
                    if abs(ind.value - price) / max(price, 0.001) < threshold
                )
                zones.append(
                    LiquidityZone(
                        market_id=swing_highs[0].market_id,
                        price_level=price,
                        zone_type=ZoneType.RESISTANCE,
                        strength=count,
                        detected_at=ts,
                    )
                )

        low_prices = [ind.value for ind in swing_lows]
        seen_lows: set[float] = set()
        for price in low_prices:
            if price in seen_lows:
                continue
            seen_lows.add(price)
            count = sum(1 for low in low_prices if abs(low - price) / max(price, 0.001) < threshold)
            if count >= 2:
                ts = max(
                    ind.timestamp
                    for ind in swing_lows
                    if abs(ind.value - price) / max(price, 0.001) < threshold
                )
                zones.append(
                    LiquidityZone(
                        market_id=swing_lows[0].market_id,
                        price_level=price,
                        zone_type=ZoneType.SUPPORT,
                        strength=count,
                        detected_at=ts,
                    )
                )

        return LiquidityZoneService._filter_zones(zones, threshold)

    @staticmethod
    def _filter_zones(
        zones: list[LiquidityZone],
        threshold: float,
    ) -> list[LiquidityZone]:
        if not zones:
            return []
        sorted_zones = sorted(zones, key=lambda z: z.price_level)
        filtered: list[LiquidityZone] = []
        current = sorted_zones[0]
        for next_zone in sorted_zones[1:]:
            price = current.price_level
            distance = (next_zone.price_level - price) / max(price, 0.001)
            if distance < threshold:
                if next_zone.strength > current.strength:
                    current = next_zone
            else:
                filtered.append(current)
                current = next_zone
        filtered.append(current)
        return filtered
