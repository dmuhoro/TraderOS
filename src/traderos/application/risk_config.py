"""WP11 (G-03) — production risk-rail configuration resolution.

The per-order rails (``RiskService.authorize_order``) already gate the real
submission seam; this module decides *which* rails are armed and refuses to
arm live trading on anything unset or invalid. Fail-closed by construction:

* Every numeric rail has a bounded sanity range; an out-of-range or
  non-numeric value is a configuration error, never silently coerced.
* Paper mode may run on the conservative dataclass defaults (explicit config
  still validated when present).
* LIVE mode additionally requires every production rail to be EXPLICITLY
  configured (settings yaml or ``RISK_*`` env override) and requires a
  mandatory, non-empty symbol allowlist. A live boot with missing or invalid
  rails raises instead of falling back to permissive defaults.

Env overrides win over yaml (same precedence as ``Config.load``):
``RISK_DAILY_LOSS_PCT``, ``RISK_MAX_GROSS_EXPOSURE``,
``RISK_MAX_POSITION_SIZE``, ``RISK_MAX_POSITIONS_TOTAL``,
``RISK_MAX_DATA_STALENESS_SECONDS``, ``RISK_ALLOWED_MARKETS``
(comma-separated symbols), ``RISK_REQUIRE_ALLOWLIST``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from typing import cast

from traderos.domain.exceptions import ConfigError
from traderos.domain.services.risk_service import DEFAULT_DAILY_LOSS_PCT

_PCT_FIELDS = ("daily_loss_pct", "max_position_size")
_EXPLICIT_LIVE_FIELDS = (
    "daily_loss_pct",
    "max_gross_exposure",
    "max_position_size",
    "max_positions_total",
)

_ENV_MAP = {
    "daily_loss_pct": "RISK_DAILY_LOSS_PCT",
    "max_gross_exposure": "RISK_MAX_GROSS_EXPOSURE",
    "max_position_size": "RISK_MAX_POSITION_SIZE",
    "max_positions_total": "RISK_MAX_POSITIONS_TOTAL",
    "max_data_staleness_seconds": "RISK_MAX_DATA_STALENESS_SECONDS",
    "allowed_markets": "RISK_ALLOWED_MARKETS",
    "require_allowlist": "RISK_REQUIRE_ALLOWLIST",
}

_RANGES: dict[str, tuple[float, float]] = {
    "daily_loss_pct": (0.0, 1.0),
    "max_position_size": (0.0, 1.0),
    "max_gross_exposure": (0.0, 10.0),
    "max_data_staleness_seconds": (0.0, 86400.0),
}


@dataclass(frozen=True)
class RiskRailSettings:
    daily_loss_pct: float = DEFAULT_DAILY_LOSS_PCT
    max_gross_exposure: float = 1.0
    max_position_size: float = 0.25
    max_positions_total: int = 10
    max_data_staleness_seconds: float = 300.0
    allowed_markets: tuple[str, ...] = field(default_factory=tuple)
    require_allowlist: bool = False
    explicit_fields: frozenset[str] = field(default_factory=frozenset)


def _env_override(name: str) -> str | None:
    value = os.getenv(_ENV_MAP[name])
    if value is None:
        return None
    value = value.strip()
    return value or None


def _to_float(raw: object, name: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise ConfigError(f"risk.{name} must be a number, got {type(raw).__name__}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"risk.{name} is not a number: {raw!r}") from exc
    lo, hi = _RANGES[name]
    if not (lo < value <= hi):
        raise ConfigError(f"risk.{name}={value} outside sane range ({lo}, {hi}]")
    return value


def _to_int(raw: object, name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise ConfigError(f"risk.{name} must be an integer, got {type(raw).__name__}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"risk.{name} is not an integer: {raw!r}") from exc
    if not (1 <= value <= 1000):
        raise ConfigError(f"risk.{name}={value} outside sane range [1, 1000]")
    return value


def _to_symbols(raw: object, name: str) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    if not isinstance(raw, list):
        raise ConfigError(f"risk.{name} must be a list of symbols or a comma-separated string")
    symbols = tuple(s for s in raw if isinstance(s, str) and s)
    if len(symbols) != len(raw):
        raise ConfigError(f"risk.{name} contains non-symbol entries")
    return symbols


def _to_bool(raw: object, name: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        if raw.strip().lower() in ("true", "1", "yes"):
            return True
        if raw.strip().lower() in ("false", "0", "no"):
            return False
    raise ConfigError(f"risk.{name} must be a boolean, got {raw!r}")


def resolve_risk_rails(risk_section: object, *, live: bool) -> RiskRailSettings:
    """Resolve + validate the production risk rails.

    ``risk_section`` is the ``risk:`` mapping from settings.yaml (may be
    absent/None). Env overrides win. Raises ``ConfigError`` listing every
    problem when anything is invalid — and, in live mode, when any production
    rail is not explicitly configured.
    """
    section: dict[str, object] = risk_section if isinstance(risk_section, dict) else {}

    def merged(name: str) -> object:
        env = _env_override(name)
        if env is not None:
            return env
        return section.get(name)

    explicit: set[str] = set()
    resolved: dict[str, object] = {}

    for name in ("daily_loss_pct", "max_gross_exposure", "max_position_size"):
        raw = merged(name)
        if raw is not None:
            resolved[name] = _to_float(raw, name)
            explicit.add(name)
    raw = merged("max_positions_total")
    if raw is not None:
        resolved["max_positions_total"] = _to_int(raw, "max_positions_total")
        explicit.add("max_positions_total")
    raw = merged("max_data_staleness_seconds")
    if raw is not None:
        resolved["max_data_staleness_seconds"] = _to_float(raw, "max_data_staleness_seconds")
        explicit.add("max_data_staleness_seconds")

    symbols: tuple[str, ...] = ()
    raw = merged("allowed_markets")
    if raw is not None:
        symbols = _to_symbols(raw, "allowed_markets")
        explicit.add("allowed_markets")

    require_allowlist = False
    raw = merged("require_allowlist")
    if raw is not None:
        require_allowlist = _to_bool(raw, "require_allowlist")
        explicit.add("require_allowlist")

    if live:
        missing = [name for name in _EXPLICIT_LIVE_FIELDS if name not in explicit]
        if missing:
            raise ConfigError(
                "LIVE mode refuses to arm without explicit production risk rails "
                f"(missing: {', '.join('risk.' + m for m in missing)}); set them in "
                "configs/settings.yaml or via RISK_* env overrides"
            )
        if not require_allowlist or not symbols:
            raise ConfigError(
                "LIVE mode requires risk.require_allowlist=true and a non-empty "
                "risk.allowed_markets list — refusing to arm with unrestricted symbols"
            )

    # Every value below was validated by _to_float/_to_int/_to_symbols above,
    # so the casts are safe: an invalid value raised a ConfigError earlier.
    daily_loss_pct = cast(float, resolved.get("daily_loss_pct", DEFAULT_DAILY_LOSS_PCT))
    max_gross_exposure = cast(float, resolved.get("max_gross_exposure", 1.0))
    max_position_size = cast(float, resolved.get("max_position_size", 0.25))
    max_positions_total = cast(int, resolved.get("max_positions_total", 10))
    max_data_staleness_seconds = cast(float, resolved.get("max_data_staleness_seconds", 300.0))

    return RiskRailSettings(
        daily_loss_pct=daily_loss_pct,
        max_gross_exposure=max_gross_exposure,
        max_position_size=max_position_size,
        max_positions_total=max_positions_total,
        max_data_staleness_seconds=max_data_staleness_seconds,
        allowed_markets=symbols,
        require_allowlist=require_allowlist,
        explicit_fields=frozenset(explicit),
    )


def validate_production_risk_settings(risk_section: object) -> list[str]:
    """Live-posture validation for the governance gate. Returns the list of
    problems (empty == PASS). Never raises — the gate reports and blocks."""
    try:
        resolve_risk_rails(risk_section, live=True)
    except ConfigError as exc:
        return [str(exc)]
    return []
