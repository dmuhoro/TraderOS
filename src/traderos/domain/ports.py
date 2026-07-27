from __future__ import annotations

from typing import Any
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class DatabasePort(Protocol):
    conn: Any

    def close(self) -> None: ...
