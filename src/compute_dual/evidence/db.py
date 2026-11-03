"""研究库（共享）：``data/research.db``。

设计文档 §4 / §6：SQLite 起步 → 可迁 Postgres。本文件是 schema 的唯一来源。

**隔离要求**：此库只放公开/内部研究数据；contacts / quotes / deal_logs 等 L2 商务表
一律不在此库（见 ``compute_dual.private.db``）。测试会断言本库 schema 里不存在这些表。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..common.errors import EvidenceGateError, InvalidTransition
from ..common.ids import claim_prefix, content_doc_id, sequence_suffix
from ..common.timeutil import now_shanghai_str
from .models import (
    EvidenceClaim,
    EpistemicTag,
    VerificationStatus,
    coerce_status,
)

SCHEMA_VERSION = "1"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 证据结论
CREATE TABLE IF NOT EXISTS evidence_claims (
    claim_id              TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL,
    statement             TEXT NOT NULL,
    epistemic_tag         TEXT NOT NULL
                          CHECK (epistemic_tag IN ('fact','inference','hypothesis','missing')),
    source_name           TEXT,
    url                   TEXT,
    published_at          TEXT,
    retrieved_at_shanghai TEXT NOT NULL,
    excerpt               TEXT,
    source_type           TEXT,
    trust_level           TEXT,
    verification_status   TEXT NOT NULL DEFAULT 'unverified'
                          CHECK (verification_status IN
                                 ('unverified','cross_checked','disputed','rejected')),
    conflict_notes        TEXT,
    doc_id                TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    -- 实现补充字段：保证既有 markdown 证据表可无损往返
    epistemic_note        TEXT,
    verification_note     TEXT,
    extra_urls            TEXT,
    -- 与模型层同一条硬闸门，防止绕过 pydantic 直接写库
    CHECK (epistemic_tag <> 'fact' OR (url IS NOT NULL AND length(url) > 0))
);

CREATE INDEX IF NOT EXISTS idx_claims_project ON evidence_claims(project_id);
CREATE INDEX IF NOT EXISTS idx_claims_tag ON evidence_claims(epistemic_tag);
CREATE INDEX IF NOT EXISTS idx_claims_status ON evidence_claims(verification_status);
CREATE INDEX IF NOT EXISTS idx_claims_url ON evidence_claims(url);
CREATE INDEX IF NOT EXISTS idx_claims_doc ON evidence_claims(doc_id);

-- 核验状态迁移留痕
CREATE TABLE IF NOT EXISTS evidence_status_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id             TEXT NOT NULL,
    from_status          TEXT,
    to_status            TEXT NOT NULL,
    note                 TEXT,
    actor                TEXT,
    occurred_at_shanghai TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES evidence_claims(claim_id) ON DELETE CASCADE
);

-- 原始资料索引（文件落在 data/raw 与 data/text）
CREATE TABLE IF NOT EXISTS documents (
    doc_id                TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL,
    url                   TEXT,
    source_name           TEXT,
    content_type          TEXT,
    http_status           INTEGER,
    sha256                TEXT NOT NULL,
    byte_size             INTEGER NOT NULL,
    page_count            INTEGER,
    retrieved_at_shanghai TEXT NOT NULL,
    raw_path              TEXT,
    text_path             TEXT,
    parse_status          TEXT NOT NULL DEFAULT 'ok',
    error_kind            TEXT,
    error_detail          TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project_id);
CREATE INDEX IF NOT EXISTS idx_documents_url ON documents(url);

-- 研究工单
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id           TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL,
    topic               TEXT NOT NULL,
    scope               TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    created_at_shanghai TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_questions (
    question_id TEXT PRIMARY KEY,
    ticket_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    question    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_claims (
    ticket_id           TEXT NOT NULL,
    question_id         TEXT NOT NULL,
    claim_id            TEXT NOT NULL,
    linked_at_shanghai  TEXT NOT NULL,
    PRIMARY KEY (ticket_id, question_id, claim_id),
    FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE,
    FOREIGN KEY (claim_id) REFERENCES evidence_claims(claim_id) ON DELETE CASCADE
);

-- 质量评测（设计 §12）
CREATE TABLE IF NOT EXISTS qa_reviews (
    review_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id          TEXT NOT NULL,
    ticket_id           TEXT,
    metric              TEXT NOT NULL,
    value               REAL NOT NULL,
    threshold           REAL,
    passed              INTEGER NOT NULL,
    detail              TEXT,
    reviewed_at_shanghai TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------- 连接


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）研究库连接。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}


# --------------------------------------------------------------------------- 证据


def next_claim_id(conn: sqlite3.Connection, project_id: str) -> str:
    """按项目现有最大序号 +1，生成 ``A-E30`` 形态的下一步 ID。"""
    prefix = claim_prefix(project_id)
    pattern = f"{prefix}-E%"
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(claim_id, ?) AS INTEGER)) AS max_seq "
        "FROM evidence_claims WHERE claim_id LIKE ?",
        (len(prefix) + 3, pattern),
    ).fetchone()
    max_seq = row["max_seq"] or 0
    return f"{prefix}-E{sequence_suffix(max_seq + 1)}"


def add_claim(conn: sqlite3.Connection, claim: EvidenceClaim) -> str:
    """写入一条证据；硬闸门在模型层与 DB CHECK 双向兜底。"""
    claim.require_url_for_fact()
    now = now_shanghai_str()
    data = claim.to_row()
    data["created_at"] = now
    data["updated_at"] = now

    columns = ", ".join(data.keys())
    placeholders = ", ".join(f":{key}" for key in data)
    try:
        conn.execute(f"INSERT INTO evidence_claims ({columns}) VALUES ({placeholders})", data)
    except sqlite3.IntegrityError as exc:
        message = str(exc)
        if "CHECK constraint failed" in message and "epistemic_tag" in message:
            raise EvidenceGateError(
                f"{claim.claim_id}: 数据库闸门拒绝 —— fact 必须有 URL"
            ) from exc
        if "UNIQUE constraint failed" in message:
            raise EvidenceGateError(f"{claim.claim_id}: claim_id 已存在，禁止覆盖（用 verify 改状态）") from exc
        raise
    conn.execute(
        "INSERT INTO evidence_status_log(claim_id, from_status, to_status, note, actor, occurred_at_shanghai)"
        " VALUES (?, NULL, ?, 'created', 'system', ?)",
        (claim.claim_id, claim.verification_status.value, now),
    )
    conn.commit()
    return claim.claim_id


def upsert_claim(conn: sqlite3.Connection, claim: EvidenceClaim) -> str:
    """存在则更新可变字段（不动 verification_status，除非显式迁移）。"""
    existing = get_claim(conn, claim.claim_id)
    if existing is None:
        return add_claim(conn, claim)
    claim.require_url_for_fact()
    data = claim.to_row()
    data["verification_status"] = existing.verification_status.value
    data["updated_at"] = now_shanghai_str()
    assignments = ", ".join(f"{key} = :{key}" for key in data if key != "claim_id")
    conn.execute(
        f"UPDATE evidence_claims SET {assignments} WHERE claim_id = :claim_id", data
    )
    conn.commit()
    return claim.claim_id


def get_claim(conn: sqlite3.Connection, claim_id: str) -> EvidenceClaim | None:
    row = conn.execute(
        "SELECT * FROM evidence_claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    return EvidenceClaim.from_row(row) if row else None


def list_claims(
    conn: sqlite3.Connection,
    *,
    project_id: str | None = None,
    tag: EpistemicTag | None = None,
    status: VerificationStatus | None = None,
) -> list[EvidenceClaim]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        params.append(project_id)
    if tag:
        clauses.append("epistemic_tag = ?")
        params.append(tag.value)
    if status:
        clauses.append("verification_status = ?")
        params.append(status.value)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM evidence_claims{where} ORDER BY claim_id", params
    ).fetchall()
    return [EvidenceClaim.from_row(row) for row in rows]


def transition_status(
    conn: sqlite3.Connection,
    claim_id: str,
    target: VerificationStatus | str,
    *,
    note: str | None = None,
    actor: str = "local",
) -> EvidenceClaim:
    """执行核验状态迁移；非法迁移抛 InvalidTransition，并留痕。

    ``target`` 接受枚举或原始字符串（``cross_checked`` / ``已对照`` 等写法）。
    """
    if not isinstance(target, VerificationStatus):
        target = coerce_status(str(target))
    claim = get_claim(conn, claim_id)
    if claim is None:
        raise EvidenceGateError(f"证据不存在：{claim_id}")
    claim.assert_can_transition(target)

    if target == claim.verification_status:
        return claim

    now = now_shanghai_str()
    conn.execute(
        "UPDATE evidence_claims SET verification_status = ?, updated_at = ? WHERE claim_id = ?",
        (target.value, now, claim_id),
    )
    conn.execute(
        "INSERT INTO evidence_status_log(claim_id, from_status, to_status, note, actor, occurred_at_shanghai)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (claim_id, claim.verification_status.value, target.value, note, actor, now),
    )
    conn.commit()
    updated = get_claim(conn, claim_id)
    assert updated is not None
    return updated


def status_history(conn: sqlite3.Connection, claim_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM evidence_status_log WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()


# --------------------------------------------------------------------------- 文档


def register_document(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    payload: bytes,
    url: str | None,
    source_name: str | None,
    content_type: str | None,
    http_status: int | None,
    retrieved_at_shanghai: str,
    raw_path: str | None,
    text_path: str | None,
    page_count: int | None = None,
    parse_status: str = "ok",
    error_kind: str | None = None,
    error_detail: str | None = None,
) -> str:
    """登记原始资料，返回 doc_id。同内容重复登记为覆盖元数据（doc_id 即内容哈希）。"""
    from ..common.ids import sha256_hex

    doc_id = content_doc_id(payload)
    conn.execute(
        "INSERT INTO documents (doc_id, project_id, url, source_name, content_type, http_status,"
        " sha256, byte_size, page_count, retrieved_at_shanghai, raw_path, text_path,"
        " parse_status, error_kind, error_detail)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(doc_id) DO UPDATE SET"
        "   url = COALESCE(excluded.url, documents.url),"
        "   source_name = COALESCE(excluded.source_name, documents.source_name),"
        "   http_status = COALESCE(excluded.http_status, documents.http_status),"
        "   raw_path = COALESCE(excluded.raw_path, documents.raw_path),"
        "   text_path = COALESCE(excluded.text_path, documents.text_path),"
        "   parse_status = excluded.parse_status,"
        "   error_kind = excluded.error_kind,"
        "   error_detail = excluded.error_detail",
        (
            doc_id,
            project_id,
            url,
            source_name,
            content_type,
            http_status,
            sha256_hex(payload),
            len(payload),
            page_count,
            retrieved_at_shanghai,
            raw_path,
            text_path,
            parse_status,
            error_kind,
            error_detail,
        ),
    )
    conn.commit()
    return doc_id


def get_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()


def list_documents(conn: sqlite3.Connection, project_id: str | None = None) -> list[sqlite3.Row]:
    if project_id:
        return conn.execute(
            "SELECT * FROM documents WHERE project_id = ? ORDER BY retrieved_at_shanghai DESC",
            (project_id,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM documents ORDER BY retrieved_at_shanghai DESC"
    ).fetchall()


def claims_for_doc(conn: sqlite3.Connection, doc_id: str) -> list[EvidenceClaim]:
    rows = conn.execute(
        "SELECT * FROM evidence_claims WHERE doc_id = ? ORDER BY claim_id", (doc_id,)
    ).fetchall()
    return [EvidenceClaim.from_row(row) for row in rows]


# --------------------------------------------------------------------------- 杂项


def insert_qa_review(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    metric: str,
    value: float,
    passed: bool,
    ticket_id: str | None = None,
    threshold: float | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO qa_reviews (project_id, ticket_id, metric, value, threshold, passed, detail,"
        " reviewed_at_shanghai) VALUES (?,?,?,?,?,?,?,?)",
        (project_id, ticket_id, metric, value, threshold, 1 if passed else 0, detail, now_shanghai_str()),
    )
    conn.commit()


def latest_qa_reviews(conn: sqlite3.Connection, project_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM qa_reviews WHERE project_id = ? ORDER BY review_id DESC LIMIT 50",
        (project_id,),
    ).fetchall()


def records_qa(conn: sqlite3.Connection, report: Any) -> None:
    """把一次 QA 报告的全部指标写入 ``qa_reviews``（供回溯与对比）。"""
    for metric in getattr(report, "metrics", []):
        insert_qa_review(
            conn,
            project_id=report.project_id,
            ticket_id=getattr(report, "ticket_id", None),
            metric=metric.key,
            value=float(metric.value),
            passed=bool(metric.passed),
            threshold=metric.threshold,
            detail=metric.detail,
        )


def count_by(conn: sqlite3.Connection, column: str, project_id: str | None = None) -> dict[str, int]:
    """按某列分组计数（列名由白名单校验，避免拼接注入）。"""
    allowed = {"epistemic_tag", "verification_status", "project_id", "source_type", "trust_level"}
    if column not in allowed:
        raise ValueError(f"不支持的分组列：{column}")
    sql = f"SELECT {column} AS key, COUNT(*) AS n FROM evidence_claims"
    params: Iterable[Any] = ()
    if project_id:
        sql += " WHERE project_id = ?"
        params = (project_id,)
    sql += " GROUP BY key ORDER BY n DESC"
    return {row["key"] or "(空)": row["n"] for row in conn.execute(sql, params).fetchall()}
