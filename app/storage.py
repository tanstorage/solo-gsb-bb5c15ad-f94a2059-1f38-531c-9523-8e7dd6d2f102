"""本地持久化：SQLite 保存规划结果。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL
);
"""


class PlanStore:
    """线程安全的 SQLite 存储。每次操作使用独立连接，避免跨线程共享。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    def init_db(self) -> None:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.executescript(_SCHEMA)

    def save(self, request: dict, result: dict) -> str:
        plan_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        with self._lock, sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT INTO plans (id, created_at, request_json, result_json)"
                " VALUES (?, ?, ?, ?)",
                (
                    plan_id,
                    created_at,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                ),
            )
        return plan_id

    def get(self, plan_id: str) -> Optional[dict]:
        with self._lock, sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, created_at, request_json, result_json FROM plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "created_at": row[1],
            "request": json.loads(row[2]),
            "result": json.loads(row[3]),
        }
