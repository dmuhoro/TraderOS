from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from traderos.domain.collectors.base import CollectorOHLCV


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DataValidator:
    MAX_GAP_PERCENT = 5.0

    @staticmethod
    def validate(data: list[CollectorOHLCV]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not data:
            errors.append("Empty data set")
            return ValidationResult(is_valid=False, errors=errors)

        for i, item in enumerate(data):
            if item.high < item.low:
                errors.append(f"Row {i}: high ({item.high}) < low ({item.low})")
            if item.open < 0 or item.high < 0 or item.low < 0 or item.close < 0:
                errors.append(f"Row {i}: negative price")
            if item.volume < 0:
                errors.append(f"Row {i}: negative volume")

        sorted_data = sorted(data, key=lambda x: x.timestamp)
        for i in range(1, len(sorted_data)):
            avg_price = (float(sorted_data[i].close) + float(sorted_data[i - 1].close)) / 2
            if avg_price > 0:
                pct_change = (
                    abs(float(sorted_data[i].close) - float(sorted_data[i - 1].close))
                    / avg_price
                    * 100
                )
                if pct_change > DataValidator.MAX_GAP_PERCENT:
                    warnings.append(
                        f"Row {i}: price gap {pct_change:.1f}% exceeds"
                        f" {DataValidator.MAX_GAP_PERCENT}%"
                    )

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
