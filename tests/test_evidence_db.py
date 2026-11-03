"""研究库 CRUD 与状态机测试（T12 验收：插入后能按 ID 取回；时区字段正确）。"""

from __future__ import annotations

import sqlite3

import pytest

from compute_dual.common.errors import EvidenceGateError, InvalidTransition
from compute_dual.common.timeutil import is_shanghai_iso, now_shanghai_str
from compute_dual.evidence import db as evdb
from compute_dual.evidence.models import EpistemicTag, EvidenceClaim, VerificationStatus


def test_insert_and_get_roundtrip(conn: sqlite3.Connection) -> None:
    claim = EvidenceClaim(
        claim_id="A-E01",
        project_id="ak",
        statement="NVIDIA 在 AI GPU 市占估计超过 80%",
        epistemic_tag="fact",
        source_name="OECD",
        url="https://example.com/oecd",
        published_at="2025-11-14",
        retrieved_at_shanghai=now_shanghai_str(),
        excerpt="over 80% market share",
        source_type="国际组织报告",
        trust_level="高",
    )
    evdb.add_claim(conn, claim)

    fetched = evdb.get_claim(conn, "A-E01")
    assert fetched is not None
    assert fetched.statement == claim.statement
    assert fetched.epistemic_tag is EpistemicTag.FACT
    assert is_shanghai_iso(fetched.retrieved_at_shanghai)


def test_duplicate_claim_id_rejected(conn: sqlite3.Connection, sample_claim) -> None:
    with pytest.raises(EvidenceGateError) as excinfo:
        evdb.add_claim(conn, sample_claim)
    assert "已存在" in str(excinfo.value)


def test_next_claim_id_sequence(conn: sqlite3.Connection) -> None:
    assert evdb.next_claim_id(conn, "ak") == "A-E01"
    assert evdb.next_claim_id(conn, "outbound") == "B-E01"

    evdb.add_claim(
        conn,
        EvidenceClaim(
            claim_id="A-E01",
            project_id="ak",
            statement="一",
            epistemic_tag="fact",
            url="https://example.com/1",
            retrieved_at_shanghai=now_shanghai_str(),
        ),
    )
    assert evdb.next_claim_id(conn, "ak") == "A-E02"

    evdb.add_claim(
        conn,
        EvidenceClaim(
            claim_id="A-E09",
            project_id="ak",
            statement="九",
            epistemic_tag="fact",
            url="https://example.com/9",
            retrieved_at_shanghai=now_shanghai_str(),
        ),
    )
    assert evdb.next_claim_id(conn, "ak") == "A-E10"


def test_db_check_constraint_blocks_fact_without_url(conn: sqlite3.Connection) -> None:
    """绕过 pydantic 直接写库，也必须被 SQLite CHECK 拦住。"""
    now = now_shanghai_str()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO evidence_claims (claim_id, project_id, statement, epistemic_tag,"
            " url, retrieved_at_shanghai, created_at, updated_at)"
            " VALUES ('A-E99','ak','绕过模型','fact',NULL,?,?,?)",
            (now, now, now),
        )


def test_status_transition_and_log(conn: sqlite3.Connection, sample_claim) -> None:
    updated = evdb.transition_status(
        conn, "A-E01", VerificationStatus.CROSS_CHECKED, note="第二来源一致"
    )
    assert updated.verification_status is VerificationStatus.CROSS_CHECKED

    history = evdb.status_history(conn, "A-E01")
    assert [row["to_status"] for row in history] == ["unverified", "cross_checked"]
    assert history[-1]["note"] == "第二来源一致"


def test_illegal_transition_rejected(conn: sqlite3.Connection, sample_claim) -> None:
    evdb.transition_status(conn, "A-E01", VerificationStatus.REJECTED)
    with pytest.raises(InvalidTransition):
        evdb.transition_status(conn, "A-E01", VerificationStatus.CROSS_CHECKED)


def test_same_status_transition_is_noop(conn: sqlite3.Connection, sample_claim) -> None:
    updated = evdb.transition_status(conn, "A-E01", VerificationStatus.UNVERIFIED)
    assert updated.verification_status is VerificationStatus.UNVERIFIED
    assert len(evdb.status_history(conn, "A-E01")) == 1


def test_transition_missing_claim(conn: sqlite3.Connection) -> None:
    with pytest.raises(EvidenceGateError):
        evdb.transition_status(conn, "A-E404", VerificationStatus.CROSS_CHECKED)


def test_list_filters(conn: sqlite3.Connection) -> None:
    for claim_id, tag, project in (
        ("A-E01", "fact", "ak"),
        ("A-E02", "inference", "ak"),
        ("B-E01", "fact", "outbound"),
    ):
        evdb.add_claim(
            conn,
            EvidenceClaim(
                claim_id=claim_id,
                project_id=project,
                statement=f"内容 {claim_id}",
                epistemic_tag=tag,
                url="https://example.com/x" if tag == "fact" else None,
                retrieved_at_shanghai=now_shanghai_str(),
            ),
        )

    assert {c.claim_id for c in evdb.list_claims(conn, project_id="ak")} == {"A-E01", "A-E02"}
    assert {c.claim_id for c in evdb.list_claims(conn, tag=EpistemicTag.FACT)} == {
        "A-E01",
        "B-E01",
    }
    assert {c.claim_id for c in evdb.list_claims(conn, status=VerificationStatus.UNVERIFIED)} == {
        "A-E01",
        "A-E02",
        "B-E01",
    }


def test_count_by_rejects_unknown_column(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        evdb.count_by(conn, "statement; DROP TABLE evidence_claims")


def test_upsert_preserves_verification_status(conn: sqlite3.Connection, sample_claim) -> None:
    evdb.transition_status(conn, "A-E01", VerificationStatus.DISPUTED, note="口径冲突")
    changed = sample_claim.model_copy(update={"statement": "改写后的结论"})
    evdb.upsert_claim(conn, changed)

    fetched = evdb.get_claim(conn, "A-E01")
    assert fetched is not None
    assert fetched.statement == "改写后的结论"
    assert fetched.verification_status is VerificationStatus.DISPUTED
