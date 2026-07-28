from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any

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


class PostgresRepository(Repository[T]):
    def __init__(self, connection: Any) -> None:
        self.conn = connection
        self._create_table()

    @property
    def _table_name(self) -> str:
        raise NotImplementedError

    @property
    def _columns(self) -> str:
        raise NotImplementedError

    def _create_table(self) -> None:
        raise NotImplementedError

    def _to_row(self, entity: T) -> dict:
        raise NotImplementedError

    def _from_row(self, row: Any) -> T:
        raise NotImplementedError

    def add(self, entity: T) -> T:
        row = self._to_row(entity)
        cols = ", ".join(row)
        placeholders = ", ".join("%s" for _ in row)
        with self.conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table_name} ({cols}) VALUES ({placeholders})",
                list(row.values()),
            )
        self.conn.commit()
        return deepcopy(entity)

    def get(self, entity_id: uuid.UUID) -> T | None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM {self._table_name} WHERE id = %s",
                (str(entity_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def list(self) -> list[T]:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._table_name}")
            rows = cur.fetchall()
        return [self._from_row(row) for row in rows]

    def update(self, entity: T) -> T:
        row = self._to_row(entity)
        set_clause = ", ".join(f"{col} = %s" for col in row if col != "id")
        values = [v for k, v in row.items() if k != "id"]
        values.append(str(row["id"]))
        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE {self._table_name} SET {set_clause} WHERE id = %s",
                values,
            )
        self.conn.commit()
        return deepcopy(entity)

    def delete(self, entity_id: uuid.UUID) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {self._table_name} WHERE id = %s",
                (str(entity_id),),
            )
        self.conn.commit()
