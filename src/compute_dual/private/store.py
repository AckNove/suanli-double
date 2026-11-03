"""私密库 ``data/private/biz_private.db``。

与研究库物理分离（两个文件、两个连接）。所有读写都要 ``Level2Context``，
所有导出都要写 ``audit_log``。
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.errors import Level2AccessDenied
from ..common.ids import new_request_id
from ..common.timeutil import now_shanghai_str
from ..config import get_settings
from .guard import Level2Context

#: 研究库绝不允许出现的表名（隔离断言用）
FORBIDDEN_IN_RESEARCH_DB = ("contacts", "quotes", "deal_logs", "matches_private", "audit_log")

#: 研究库绝不允许出现的列名（防止把联系方式塞进证据表）
FORBIDDEN_COLUMNS_IN_RESEARCH = ("email", "phone", "wechat", "mobile", "unit_price", "contact_name")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id   TEXT PRIMARY KEY,
    subject_id   TEXT NOT NULL,           -- SUP-… / DEM-…
    project_id   TEXT NOT NULL DEFAULT 'outbound',
    name         TEXT NOT NULL,
    title        TEXT,
    email        TEXT,
    phone        TEXT,
    wechat       TEXT,
    org          TEXT,
    notes        TEXT,
    sensitivity  TEXT NOT NULL DEFAULT 'L2',
    created_at_shanghai TEXT NOT NULL,
    updated_at_shanghai TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id     TEXT PRIMARY KEY,
    subject_id   TEXT NOT NULL,
    project_id   TEXT NOT NULL DEFAULT 'outbound',
    gpu_model    TEXT,
    unit         TEXT,
    unit_price   REAL,
    currency     TEXT DEFAULT 'USD',
    quantity     INTEGER,
    valid_until  TEXT,
    incoterm     TEXT,
    notes        TEXT,
    sensitivity  TEXT NOT NULL DEFAULT 'L2',
    created_at_shanghai TEXT NOT NULL,
    updated_at_shanghai TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deal_logs (
    deal_id      TEXT PRIMARY KEY,
    match_id     TEXT,
    supplier_id  TEXT,
    demand_id    TEXT,
    stage        TEXT NOT NULL,
    summary      TEXT,
    occurred_at_shanghai TEXT NOT NULL,
    owner        TEXT,
    created_at_shanghai TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS matches_private (
    match_id     TEXT PRIMARY KEY,
    supplier_id  TEXT NOT NULL,
    demand_id    TEXT NOT NULL,
    sensitive_notes TEXT,
    exclusivity  TEXT,
    created_at_shanghai TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_shanghai  TEXT NOT NULL,
    actor        TEXT NOT NULL,
    purpose      TEXT NOT NULL,
    action       TEXT NOT NULL,
    table_name   TEXT,
    object_id    TEXT,
    row_count    INTEGER,
    allow_export INTEGER NOT NULL DEFAULT 0,
    request_id   TEXT,
    detail       TEXT
);
"""

ALLOWED_TABLES = ("contacts", "quotes", "deal_logs", "matches_private")


@dataclass
class AuditEntry:
    action: str
    table_name: str | None = None
    object_id: str | None = None
    row_count: int | None = None
    detail: str | None = None


class PrivateStore:
    """私密库访问对象：每次操作都要带 L2 上下文。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        settings = get_settings()
        settings.private_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else settings.private_db
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # ---------------------------------------------------------------- 连接

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA_SQL)
            connection.commit()
            self._conn = connection
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "PrivateStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def init(self, context: Level2Context) -> None:
        """显式建表（幂等）+ 审计。"""
        self.conn
        self._audit(context, AuditEntry(action="init_schema"))

    # ---------------------------------------------------------------- 审计

    def _audit(self, context: Level2Context, entry: AuditEntry) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts_shanghai, actor, purpose, action, table_name, object_id,"
            " row_count, allow_export, request_id, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                now_shanghai_str(),
                context.actor,
                context.purpose,
                entry.action,
                entry.table_name,
                entry.object_id,
                entry.row_count,
                1 if context.allow_export else 0,
                new_request_id(),
                entry.detail,
            ),
        )
        self.conn.commit()

    def audit_entries(self, context: Level2Context, limit: int = 20) -> list[sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY audit_id DESC LIMIT ?", (limit,)
        ).fetchall()
        self._audit(context, AuditEntry(action="read_audit_log", table_name="audit_log", row_count=len(rows)))
        return rows

    # ---------------------------------------------------------------- 写

    def add_contact(
        self,
        context: Level2Context,
        *,
        subject_id: str,
        name: str,
        title: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        wechat: str | None = None,
        org: str | None = None,
        notes: str | None = None,
        project_id: str = "outbound",
    ) -> str:
        if not subject_id.strip() or not name.strip():
            raise ValueError("subject_id 与 name 必填")
        now = now_shanghai_str()
        contact_id = f"CON-{now[:10].replace('-', '')}-{self._next_seq('contacts', 'contact_id', 'CON')}"
        self.conn.execute(
            "INSERT INTO contacts (contact_id, subject_id, project_id, name, title, email, phone,"
            " wechat, org, notes, created_at_shanghai, updated_at_shanghai)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                contact_id,
                subject_id,
                project_id,
                name,
                title,
                email,
                phone,
                wechat,
                org,
                notes,
                now,
                now,
            ),
        )
        self.conn.commit()
        self._audit(
            context,
            AuditEntry(action="insert", table_name="contacts", object_id=contact_id, row_count=1),
        )
        return contact_id

    def add_quote(
        self,
        context: Level2Context,
        *,
        subject_id: str,
        gpu_model: str | None = None,
        unit: str | None = None,
        unit_price: float | None = None,
        currency: str = "USD",
        quantity: int | None = None,
        valid_until: str | None = None,
        incoterm: str | None = None,
        notes: str | None = None,
        project_id: str = "outbound",
    ) -> str:
        now = now_shanghai_str()
        quote_id = f"QT-{now[:10].replace('-', '')}-{self._next_seq('quotes', 'quote_id', 'QT')}"
        self.conn.execute(
            "INSERT INTO quotes (quote_id, subject_id, project_id, gpu_model, unit, unit_price,"
            " currency, quantity, valid_until, incoterm, notes, created_at_shanghai, updated_at_shanghai)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                quote_id,
                subject_id,
                project_id,
                gpu_model,
                unit,
                unit_price,
                currency,
                quantity,
                valid_until,
                incoterm,
                notes,
                now,
                now,
            ),
        )
        self.conn.commit()
        self._audit(
            context, AuditEntry(action="insert", table_name="quotes", object_id=quote_id, row_count=1)
        )
        return quote_id

    def add_deal_log(
        self,
        context: Level2Context,
        *,
        stage: str,
        match_id: str | None = None,
        supplier_id: str | None = None,
        demand_id: str | None = None,
        summary: str | None = None,
        owner: str | None = None,
    ) -> str:
        now = now_shanghai_str()
        deal_id = f"DEAL-{now[:10].replace('-', '')}-{self._next_seq('deal_logs', 'deal_id', 'DEAL')}"
        self.conn.execute(
            "INSERT INTO deal_logs (deal_id, match_id, supplier_id, demand_id, stage, summary,"
            " occurred_at_shanghai, owner, created_at_shanghai) VALUES (?,?,?,?,?,?,?,?,?)",
            (deal_id, match_id, supplier_id, demand_id, stage, summary, now, owner, now),
        )
        self.conn.commit()
        self._audit(
            context, AuditEntry(action="insert", table_name="deal_logs", object_id=deal_id, row_count=1)
        )
        return deal_id

    def _next_seq(self, table: str, id_column: str, prefix: str) -> str:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"不支持的 table：{table}")
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {id_column} LIKE ?", (f"{prefix}-%",)
        ).fetchone()
        return f"{(row['n'] or 0) + 1:04d}"

    # ---------------------------------------------------------------- 读 / 导出

    def count(self, context: Level2Context, table: str) -> int:
        if table not in ALLOWED_TABLES:
            raise ValueError(f"不支持的 table：{table}（可用：{', '.join(ALLOWED_TABLES)}）")
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        total = int(row["n"] or 0)
        self._audit(
            context, AuditEntry(action="count", table_name=table, row_count=total)
        )
        return total

    def export_rows(
        self,
        context: Level2Context,
        table: str,
        *,
        out_path: str | Path,
        fmt: str = "csv",
    ) -> Path:
        """导出 L2 表。必须显式 allow_export，且写审计。"""
        if not context.allow_export:
            raise Level2AccessDenied(
                "导出 L2 数据需要显式授权：请带 --allow-export，否则拒绝（防止误推到共享区）"
            )
        if table not in ALLOWED_TABLES:
            raise ValueError(f"不支持的 table：{table}")

        cursor = self.conn.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description or []]
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if fmt.lower() == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row[key] for key in columns})
            # 加 BOM 便于 Excel 打开中文
            path.write_text("\ufeff" + buffer.getvalue(), encoding="utf-8")
        elif fmt.lower() == "json":
            import json

            payload = [
                {key: row[key] for key in columns} for row in rows
            ]
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            raise ValueError(f"不支持的导出格式：{fmt}（可用 csv / json）")

        self._audit(
            context,
            AuditEntry(
                action="export",
                table_name=table,
                row_count=len(rows),
                detail=f"fmt={fmt}; out={path}",
            ),
        )
        return path

    def audit_self_check(self, context: Level2Context) -> dict[str, Any]:
        """导出后自查：本次会话是否有导出记录（用于验收演示）。"""
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE action = 'export' ORDER BY audit_id DESC LIMIT 5"
        ).fetchall()
        return {"export_count": len(rows), "latest": [dict(row) for row in rows]}


# --------------------------------------------------------------------------- 隔离验证


def verify_isolation(research_db_path: str | Path, private_db_path: str | Path) -> dict[str, Any]:
    """证明研究库与私密库确实隔离（供测试与 ``ak doctor`` 使用）。

    检查三件事：
    1. 研究库里不存在 L2 表名；
    2. 研究库里不存在敏感列名；
    3. 用同一条 SELECT 分别打两个库，研究库必须失败。
    """
    research_path = Path(research_db_path)
    private_path = Path(private_db_path)

    report: dict[str, Any] = {
        "research_db": str(research_path),
        "private_db": str(private_path),
        "research_db_exists": research_path.exists(),
        "private_db_exists": private_path.exists(),
        "forbidden_tables_found": [],
        "forbidden_columns_found": [],
        "cross_query_blocked": None,
        "same_file": research_path.resolve() == private_path.resolve() if research_path.exists() else False,
    }

    if research_path.exists():
        connection = sqlite3.connect(research_path)
        connection.row_factory = sqlite3.Row
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            report["forbidden_tables_found"] = sorted(tables & set(FORBIDDEN_IN_RESEARCH_DB))

            for table in tables:
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                hit = columns & set(FORBIDDEN_COLUMNS_IN_RESEARCH)
                if hit:
                    report["forbidden_columns_found"].append({table: sorted(hit)})

            try:
                connection.execute("SELECT contact_id FROM contacts LIMIT 1").fetchone()
                report["cross_query_blocked"] = False
            except sqlite3.OperationalError:
                report["cross_query_blocked"] = True
        finally:
            connection.close()

    report["isolated"] = bool(
        not report["same_file"]
        and not report["forbidden_tables_found"]
        and not report["forbidden_columns_found"]
        and report["cross_query_blocked"] is not False
    )
    return report
