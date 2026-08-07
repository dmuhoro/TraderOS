from __future__ import annotations

import uuid

import pytest

from traderos.domain.services.risk_service import PerUserRiskProfile
from traderos.domain.services.risk_service import PerUserRiskResolver
from traderos.domain.services.risk_service import RiskService

MID = uuid.uuid4()


class _Audit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, str, str]] = []

    def record(self, action: str, actor: str, resource: str, detail: str = "") -> None:
        self.entries.append((action, actor, detail))


class _Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = {}

    def counter(self, name: str, delta: float = 1.0) -> float:
        self.counters[name] = self.counters.get(name, 0.0) + delta
        return self.counters[name]

    def get_counter(self, name: str) -> float:
        return self.counters.get(name, 0.0)


@pytest.fixture()
def audit() -> _Audit:
    return _Audit()


@pytest.fixture()
def metrics() -> _Metrics:
    return _Metrics()


def _new(
    profile: PerUserRiskProfile | None,
    audit: object,
    metrics: object | None = None,
) -> RiskService:
    resolver = None
    if profile is not None:
        resolver = PerUserRiskResolver({profile.user_id: profile})
    return RiskService(audit=audit, metrics=metrics, user_resolver=resolver)


def _with_resolver(audit: object, metrics: object | None = None) -> RiskService:
    return RiskService(audit=audit, metrics=metrics, user_resolver=PerUserRiskResolver())


class TestPerUserProfile:
    def test_profile_fail_closed_defaults_bounded(self) -> None:
        p = PerUserRiskProfile(user_id="u-1")
        assert p.max_gross_exposure == 1.0
        assert p.max_position_size == 0.25
        assert p.daily_loss_pct == 0.02
        assert p.engaged is False

    def test_resolver_unknown_returns_none(self) -> None:
        resolver = PerUserRiskResolver()
        assert resolver.resolve("u-x") is None

    def test_resolver_known_returns(self) -> None:
        p = PerUserRiskProfile(user_id="u-1", daily_loss_pct=0.01)
        resolver = PerUserRiskResolver({"u-1": p})
        assert resolver.resolve("u-1") == p


class TestPerUserFailClosed:
    def test_unknown_user_order_is_denied(self, audit: _Audit, metrics: _Metrics) -> None:
        risk = _with_resolver(audit, metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="ghost")
        assert verdict.allowed is False
        assert "No per-user risk profile" in verdict.reason
        assert any(
            action == "risk.user_order_blocked" and actor == "ghost"
            for action, actor, _ in audit.entries
        )
        assert metrics.get_counter("risk.user_order_blocked") == 1.0

    def test_unknown_user_flatten_is_denied(self, audit: _Audit, metrics: _Metrics) -> None:
        risk = _with_resolver(audit, metrics)
        verdict = risk.can_trade([], user_id="ghost")
        assert verdict.allowed is False
        assert any(actor == "ghost" for _, actor, _ in audit.entries)


class TestUserAttributionAndRails:
    def test_user_allowlist_blocks_unlisted(self, audit: _Audit, metrics: _Metrics) -> None:
        other = uuid.uuid4()
        profile = PerUserRiskProfile(user_id="u-1", allowed_markets=frozenset({other}))
        risk = _new(profile, audit, metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="u-1")
        assert verdict.allowed is False
        assert "allowlist" in verdict.reason
        assert any(actor == "u-1" for _, actor, _ in audit.entries)

    def test_user_position_cap_applied(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", max_position_size=0.05)
        risk = _new(profile, audit, metrics)
        verdict = risk.authorize_order(MID, "buy", 50.0, 100.0, equity=5000.0, user_id="u-1")
        assert verdict.allowed is False
        assert "max_position_size" in verdict.reason
        assert any(actor == "u-1" for _, actor, _ in audit.entries)

    def test_user_gross_cap_applied(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", max_gross_exposure=0.10)
        risk = _new(profile, audit, metrics)
        verdict = risk.authorize_order(
            MID,
            "buy",
            10.0,
            100.0,
            equity=10000.0,
            existing_gross_exposure=1000.0,
            user_id="u-1",
        )
        assert verdict.allowed is False
        assert "gross exposure" in verdict.reason
        assert any(actor == "u-1" for _, actor, _ in audit.entries)

    def test_known_user_allowed_and_attributed(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", allowed_markets=frozenset({MID}))
        risk = _new(profile, audit, metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="u-1")
        assert verdict.allowed is True
        assert metrics.get_counter("risk.order_allowed") == 1.0

    def test_global_path_unchanged_system_attribution(
        self, audit: _Audit, metrics: _Metrics
    ) -> None:
        risk = RiskService(audit=audit, metrics=metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0)
        assert verdict.allowed is True

    def test_user_position_limit_in_can_trade(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", max_positions_total=2)
        risk = _new(profile, audit, metrics)
        positions = [_position(1), _position(2), _position(3)]
        verdict = risk.can_trade(positions, user_id="u-1")
        assert verdict.allowed is False
        assert "Max positions (2)" in verdict.reason


class TestPerUserKillSwitchScoping:
    def test_engaged_user_order_denied(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", engaged=True)
        risk = _new(profile, audit, metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="u-1")
        assert verdict.allowed is False
        assert "Kill switch engaged for this trader" in verdict.reason
        assert any(
            action == "risk.user_order_blocked" and actor == "u-1"
            for action, actor, _ in audit.entries
        )

    def test_engaged_user_can_trade_denied(self, audit: _Audit, metrics: _Metrics) -> None:
        profile = PerUserRiskProfile(user_id="u-1", engaged=True)
        risk = _new(profile, audit, metrics)
        verdict = risk.can_trade([], user_id="u-1")
        assert verdict.allowed is False
        assert "Kill switch engaged for this trader" in verdict.reason

    def test_scoped_kill_does_not_block_other_users(self, audit: _Audit, metrics: _Metrics) -> None:
        engaged = PerUserRiskProfile(user_id="u-1", engaged=True, allowed_markets=frozenset({MID}))
        active = PerUserRiskProfile(user_id="u-2", allowed_markets=frozenset({MID}))
        risk = RiskService(
            audit=audit,
            metrics=metrics,
            user_resolver=PerUserRiskResolver({"u-1": engaged, "u-2": active}),
        )
        v1 = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="u-1")
        v2 = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0, user_id="u-2")
        assert v1.allowed is False
        assert v2.allowed is True

    def test_scoped_kill_without_user_resolver_global_path_unchanged(
        self, audit: _Audit, metrics: _Metrics
    ) -> None:
        risk = RiskService(audit=audit, metrics=metrics)
        verdict = risk.authorize_order(MID, "buy", 1.0, 100.0, equity=10000.0)
        assert verdict.allowed is True


def _position(i: int):
    from traderos.domain.entities import Position

    return Position(
        market_id=MID,
        quantity=1.0,
        entry_price=100.0 + i,
        current_price=100.0 + i,
        pnl=0.0,
    )
