"""L2 隔离与审计测试（T16 验收：研究库查不到联系人字段）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from compute_dual.common.errors import Level2AccessDenied
from compute_dual.evidence import db as evdb
from compute_dual.private.guard import require_l2_access
from compute_dual.private.store import (
    FORBIDDEN_COLUMNS_IN_RESEARCH,
    FORBIDDEN_IN_RESEARCH_DB,
    PrivateStore,
    verify_isolation,
)


@pytest.fixture()
def store(tmp_path: Path):
    with PrivateStore(tmp_path / "private" / "biz_private.db") as instance:
        yield instance


def test_research_db_has_no_l2_tables(conn: sqlite3.Connection) -> None:
    tables = evdb.table_names(conn)
    assert not (tables & set(FORBIDDEN_IN_RESEARCH_DB))


def test_research_db_has_no_sensitive_columns(conn: sqlite3.Connection) -> None:
    for table in evdb.table_names(conn):
        columns = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        assert not (columns & set(FORBIDDEN_COLUMNS_IN_RESEARCH))


def test_cross_query_is_blocked(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT contact_id FROM contacts LIMIT 1").fetchone()


def test_verify_isolation_reports_isolated(settings) -> None:
    evdb.init_schema(evdb.connect(settings.research_db))
    with PrivateStore(settings.private_db) as private_store:
        private_store.init(require_l2_access("隔离验证"))

    report = verify_isolation(settings.research_db, settings.private_db)
    assert report["isolated"] is True
    assert report["forbidden_tables_found"] == []
    assert report["forbidden_columns_found"] == []
    assert report["cross_query_blocked"] is True
    assert report["same_file"] is False


def test_l2_access_requires_purpose() -> None:
    with pytest.raises(Level2AccessDenied):
        require_l2_access(None)
    with pytest.raises(Level2AccessDenied):
        require_l2_access("   ")
    with pytest.raises(Level2AccessDenied):
        require_l2_access("abc")  # 太短，视为无效用途


def test_l2_access_ok() -> None:
    context = require_l2_access("商务跟进：SUP-001 澄清")
    assert context.purpose.startswith("商务跟进")
    assert context.allow_export is False


def test_l2_lock_via_env(monkeypatch) -> None:
    monkeypatch.setenv("COMPUTE_L2_LOCKED", "1")
    with pytest.raises(Level2AccessDenied):
        require_l2_access("任何用途")


def test_contacts_live_only_in_private_db(store: PrivateStore, settings) -> None:
    context = require_l2_access("录入联系人")
    contact_id = store.add_contact(
        context,
        subject_id="SUP-20261102-001",
        name="合成联系人",
        email="syn@example.com",
        phone="+86-000-0000-0000",
    )
    assert contact_id.startswith("CON-")

    row = store.conn.execute(
        "SELECT email FROM contacts WHERE contact_id = ?", (contact_id,)
    ).fetchone()
    assert row["email"] == "syn@example.com"

    # 研究库里没有任何地方存得下这个邮箱
    research = evdb.connect(settings.research_db)
    try:
        evdb.init_schema(research)
        dump = "\n".join(
            research.iterdump()
        )
    finally:
        research.close()
    assert "syn@example.com" not in dump


def test_export_requires_explicit_permission(store: PrivateStore, tmp_path: Path) -> None:
    context = require_l2_access("尝试导出但未授权")
    with pytest.raises(Level2AccessDenied):
        store.export_rows(context, "contacts", out_path=tmp_path / "contacts.csv")


def test_export_writes_audit_row(store: PrivateStore, tmp_path: Path) -> None:
    write_context = require_l2_access("录入合成联系人")
    store.add_contact(write_context, subject_id="DEM-20261102-005", name="合成联系人B")

    export_context = require_l2_access("向Arthur汇报", allow_export=True)
    out = store.export_rows(export_context, "contacts", out_path=tmp_path / "contacts.csv")
    assert out.exists()
    assert "合成联系人B" in out.read_text(encoding="utf-8-sig")

    audit_rows = store.conn.execute(
        "SELECT * FROM audit_log WHERE action = 'export'"
    ).fetchall()
    assert len(audit_rows) == 1
    assert audit_rows[0]["allow_export"] == 1
    assert audit_rows[0]["row_count"] == 1
    assert audit_rows[0]["purpose"] == "向Arthur汇报"


def test_audit_log_records_inserts(store: PrivateStore) -> None:
    context = require_l2_access("录入报价")
    quote_id = store.add_quote(
        context, subject_id="SUP-20261102-001", gpu_model="H100", unit="卡/月", unit_price=1234.5
    )
    assert quote_id.startswith("QT-")

    actions = [row["action"] for row in store.conn.execute("SELECT action FROM audit_log")]
    assert "insert" in actions


def test_unsupported_table_rejected(store: PrivateStore) -> None:
    context = require_l2_access("尝试越权")
    with pytest.raises(ValueError):
        store.count(context, "evidence_claims")


def test_contact_requires_name(store: PrivateStore) -> None:
    context = require_l2_access("录入合成联系人")
    with pytest.raises(ValueError):
        store.add_contact(context, subject_id="SUP-1", name="  ")
