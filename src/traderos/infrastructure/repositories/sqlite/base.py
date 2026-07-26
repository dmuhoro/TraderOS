from __future__ import annotations

import json
import sqlite3
import uuid
from copy import deepcopy
from datetime import datetime

from traderos.domain.repositories.base import Repository
from traderos.domain.repositories.base import T


def to_uuid(val: str | uuid.UUID) -> uuid.UUID:
    return val if isinstance(val, uuid.UUID) else uuid.UUID(val)


def to_dt(val: str | datetime) -> datetime:
    return val if isinstance(val, datetime) else datetime.fromisoformat(val)


def to_json(val: object) -> str:
    return json.dumps(val, default=str)


def from_json(val: str | None) -> object:
    if val is None:
        return None
    return json.loads(val)


class SQLiteRepository(Repository[T]):
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection
        self.conn.row_factory = sqlite3.Row
        self._create_table()

    @property
    def _table_name(self) -> str:
        return type(self).__name__.replace("SQLite", "").lower()

    @property
    def _columns(self) -> str:
        raise NotImplementedError

    def _create_table(self) -> None:
        raise NotImplementedError

    def _to_row(self, entity: T) -> dict:
        raise NotImplementedError

    def _from_row(self, row: sqlite3.Row) -> T:
        raise NotImplementedError

    def add(self, entity: T) -> T:
        row = self._to_row(entity)
        cols = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        self.conn.execute(
            f"INSERT INTO {self._table_name} ({cols}) VALUES ({placeholders})",
            list(row.values()),
        )
        self.conn.commit()
        return deepcopy(entity)

    def get(self, entity_id: uuid.UUID) -> T | None:
        cursor = self.conn.execute(
            f"SELECT * FROM {self._table_name} WHERE id = ?",
            (str(entity_id),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list(self) -> list[T]:
        cursor = self.conn.execute(f"SELECT * FROM {self._table_name}")
        return [self._from_row(row) for row in cursor.fetchall()]

    def update(self, entity: T) -> T:
        row = self._to_row(entity)
        set_clause = ", ".join(f"{col} = ?" for col in row if col != "id")
        values = [v for k, v in row.items() if k != "id"]
        values.append(str(row["id"]))
        self.conn.execute(
            f"UPDATE {self._table_name} SET {set_clause} WHERE id = ?",
            values,
        )
        self.conn.commit()
        return deepcopy(entity)

    def delete(self, entity_id: uuid.UUID) -> None:
        self.conn.execute(
            f"DELETE FROM {self._table_name} WHERE id = ?",
            (str(entity_id),),
        )
        self.conn.commit()
