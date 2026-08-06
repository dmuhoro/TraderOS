from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TypeVar


class CollectorType(Enum):
    BINANCE = "binance"
    ALPACA = "alpaca"
    YFINANCE = "yfinance"
    MOCK = "mock"
    STREAMING = "streaming"


T = TypeVar("T", bound="DataCollector")


@dataclass(frozen=True)
class CollectorOHLCV:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: datetime
    symbol: str


class DataCollector(ABC):
    @property
    @abstractmethod
    def collector_type(self) -> CollectorType: ...

    @abstractmethod
    def fetch_historical(
        self,
        symbol: str,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 500,
    ) -> list[CollectorOHLCV]: ...

    @abstractmethod
    def validate_symbol(self, symbol: str) -> bool: ...


class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[CollectorType, DataCollector] = {}

    def register(self, collector: DataCollector) -> None:
        self._collectors[collector.collector_type] = collector

    def get(self, collector_type: CollectorType) -> DataCollector | None:
        return self._collectors.get(collector_type)

    def list_types(self) -> list[CollectorType]:
        return list(self._collectors)

    def unregister(self, collector_type: CollectorType) -> None:
        self._collectors.pop(collector_type, None)
