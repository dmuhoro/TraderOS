from __future__ import annotations

import uuid
from datetime import UTC
from datetime import datetime

from traderos.domain.entities import Indicator
from traderos.domain.entities import ZoneType
from traderos.domain.services.liquidity_zone_service import LiquidityZoneService

_MID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _ind(value: float, ts: datetime) -> Indicator:
    return Indicator(market_id=_MID, timestamp=ts, name="swing", value=value)


def _ts(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=UTC)


class TestLiquidityZoneService:
    def test_empty_swings(self) -> None:
        assert LiquidityZoneService.map_zones_from_swings([], []) == []

    def test_resistance_zone_from_clustered_highs(self) -> None:
        highs = [_ind(100, _ts(1)), _ind(100.05, _ts(2)), _ind(100.1, _ts(3))]
        zones = LiquidityZoneService.map_zones_from_swings(highs, [], threshold=0.002)
        assert len(zones) == 1
        assert zones[0].zone_type == ZoneType.RESISTANCE
        assert zones[0].strength == 3

    def test_support_zone_from_clustered_lows(self) -> None:
        lows = [_ind(90, _ts(1)), _ind(89.95, _ts(2)), _ind(90.05, _ts(3))]
        zones = LiquidityZoneService.map_zones_from_swings([], lows, threshold=0.002)
        assert len(zones) == 1
        assert zones[0].zone_type == ZoneType.SUPPORT
        assert zones[0].strength >= 2

    def test_insufficient_strength(self) -> None:
        highs = [_ind(100, _ts(1)), _ind(110, _ts(2))]
        zones = LiquidityZoneService.map_zones_from_swings(highs, [], threshold=0.002)
        assert zones == []

    def test_combined_resistance_and_support(self) -> None:
        highs = [_ind(100, _ts(1)), _ind(100.1, _ts(2)), _ind(99.9, _ts(3))]
        lows = [_ind(80, _ts(1)), _ind(80.05, _ts(2)), _ind(79.95, _ts(3))]
        zones = LiquidityZoneService.map_zones_from_swings(highs, lows, threshold=0.002)
        assert len(zones) == 2
        zone_types = {z.zone_type for z in zones}
        assert zone_types == {ZoneType.RESISTANCE, ZoneType.SUPPORT}

    def test_zone_has_correct_metadata(self) -> None:
        highs = [_ind(100, _ts(1)), _ind(100.05, _ts(2))]
        zones = LiquidityZoneService.map_zones_from_swings(highs, [], threshold=0.01)
        assert len(zones) == 1
        zone = zones[0]
        assert zone.market_id == _MID
        assert zone.price_level == 100.0
        assert zone.zone_type == ZoneType.RESISTANCE
        assert zone.strength == 2
        assert zone.detected_at == _ts(2)

    def test_filter_nearby_zones(self) -> None:
        highs = [
            _ind(100, _ts(1)),
            _ind(100.5, _ts(2)),  # cluster ~100
            _ind(110, _ts(3)),
            _ind(110.4, _ts(4)),  # cluster ~110
        ]
        zones = LiquidityZoneService.map_zones_from_swings(highs, [], threshold=0.01)
        assert len(zones) == 2
        assert zones[0].price_level == 100.0
        assert zones[1].price_level == 110.0

    def test_duplicate_high_price_counts_once(self) -> None:
        highs = [_ind(100, _ts(1)), _ind(100, _ts(2)), _ind(100.05, _ts(3))]
        zones = LiquidityZoneService.map_zones_from_swings(highs, [], threshold=0.002)
        assert len(zones) == 1
        assert zones[0].strength == 3

    def test_duplicate_low_price_counts_once(self) -> None:
        lows = [_ind(90, _ts(1)), _ind(90, _ts(2)), _ind(90.05, _ts(3))]
        zones = LiquidityZoneService.map_zones_from_swings([], lows, threshold=0.002)
        assert len(zones) == 1
        assert zones[0].strength == 3
