"""研究工单工作流与草稿生成测试（T14 验收：草稿带 claim_id 引用）。"""

from __future__ import annotations

import sqlite3

import pytest

from compute_dual.common.timeutil import now_shanghai_str
from compute_dual.evidence import db as evdb
from compute_dual.evidence.models import EvidenceClaim
from compute_dual.research.draft import build_draft, write_draft
from compute_dual.research.ingest import ingest_file
from compute_dual.research.qa import run_qa
from compute_dual.research.tickets import (
    add_question,
    create_ticket,
    get_ticket,
    link_claim,
    list_tickets,
)


def _add(conn: sqlite3.Connection, claim_id: str, tag: str, *, url: str | None, excerpt: str | None):
    claim = EvidenceClaim(
        claim_id=claim_id,
        project_id="ak",
        statement=f"结论 {claim_id}",
        epistemic_tag=tag,
        source_name="合成来源",
        url=url,
        retrieved_at_shanghai=now_shanghai_str(),
        excerpt=excerpt,
    )
    evdb.add_claim(conn, claim)
    return claim


@pytest.fixture()
def ticket(conn: sqlite3.Connection):
    created = create_ticket(conn, project_id="ak", topic="合成研究主题", scope="只用公开信息")
    question_a = add_question(conn, created.ticket_id, "问题甲")
    question_b = add_question(conn, created.ticket_id, "问题乙（无证据）")
    return created, question_a, question_b


def test_create_ticket_and_questions(conn: sqlite3.Connection, ticket) -> None:
    created, question_a, question_b = ticket
    assert created.ticket_id.startswith("T-")
    assert question_a.question_id == f"{created.ticket_id}-Q1"
    assert question_b.question_id == f"{created.ticket_id}-Q2"

    loaded = get_ticket(conn, created.ticket_id)
    assert loaded is not None
    assert len(loaded.questions) == 2


def test_ticket_ids_increment_within_day(conn: sqlite3.Connection, ticket) -> None:
    created, _, _ = ticket
    second = create_ticket(conn, project_id="ak", topic="第二个主题")
    assert second.ticket_id != created.ticket_id
    assert len(list_tickets(conn, "ak")) == 2


def test_link_claim_validates(conn: sqlite3.Connection, ticket) -> None:
    created, question_a, _ = ticket
    _add(conn, "A-E01", "fact", url="https://example.com/a", excerpt="摘录")

    link_claim(conn, created.ticket_id, question_a.question_id, "A-E01")

    with pytest.raises(ValueError):
        link_claim(conn, created.ticket_id, question_a.question_id, "A-E99")

    with pytest.raises(ValueError):
        link_claim(conn, "T-19990101-001", question_a.question_id, "A-E01")


def test_draft_cites_claims_and_flags_gaps(conn: sqlite3.Connection, ticket, tmp_path) -> None:
    created, question_a, question_b = ticket
    _add(conn, "A-E01", "fact", url="https://example.com/a", excerpt="权威摘录")
    _add(conn, "A-E02", "missing", url=None, excerpt=None)
    link_claim(conn, created.ticket_id, question_a.question_id, "A-E01")
    link_claim(conn, created.ticket_id, question_a.question_id, "A-E02")

    result = build_draft(conn, created.ticket_id)

    assert "[@A-E01]" in result.markdown
    assert "A-E01" in result.cited_claim_ids
    assert "数据缺口汇总" in result.markdown
    assert any("无任何证据" in gap for gap in result.gaps)
    assert any("A-E02" in gap for gap in result.gaps)
    assert "结论（待人工撰写）" in result.markdown
    assert result.qa.publishable is False, "有缺失占位时不得标可对外"

    out = write_draft(conn, created.ticket_id, tmp_path / "draft.md")
    assert out.markdown == result.markdown
    assert (tmp_path / "draft.md").exists()


def test_draft_marks_disputed_conflict(conn: sqlite3.Connection, ticket) -> None:
    created, question_a, _ = ticket
    _add(conn, "A-E03", "fact", url="https://example.com/a", excerpt="摘录")
    evdb.transition_status(conn, "A-E03", "disputed", note="与另一来源口径冲突")
    link_claim(conn, created.ticket_id, question_a.question_id, "A-E03")

    result = build_draft(conn, created.ticket_id)
    assert "冲突待消" in result.markdown
    assert any("口径冲突" in gap for gap in result.gaps)


def test_draft_unknown_ticket(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        build_draft(conn, "T-19990101-001")


def test_qa_blocks_without_excerpt(conn: sqlite3.Connection) -> None:
    _add(conn, "A-E01", "fact", url="https://example.com/a", excerpt=None)
    report = run_qa(conn, "ak")

    metrics = {metric.key: metric for metric in report.metrics}
    assert metrics["no_excerpt_ratio"].passed is False
    assert report.publishable is False


def test_qa_passes_clean_project(conn: sqlite3.Connection) -> None:
    _add(conn, "A-E01", "fact", url="https://example.com/a", excerpt="摘录")
    evdb.transition_status(conn, "A-E01", "cross_checked", note="两源一致")

    report = run_qa(conn, "ak")
    assert report.publishable is True
    assert report.blocking_reasons == []


def test_ingest_fixture_then_cite(conn: sqlite3.Connection, fixtures_dir, ticket) -> None:
    created, question_a, _ = ticket
    result = ingest_file(conn, fixtures_dir / "sample.pdf", project_id="ak")
    assert result.ok and result.doc_id

    _add(conn, "A-E10", "fact", url="https://example.com/a", excerpt="摘录")
    conn.execute(
        "UPDATE evidence_claims SET doc_id = ? WHERE claim_id = 'A-E10'", (result.doc_id,)
    )
    conn.commit()
    link_claim(conn, created.ticket_id, question_a.question_id, "A-E10")

    assert evdb.claims_for_doc(conn, result.doc_id)[0].claim_id == "A-E10"


def test_ingest_missing_file_reports_failure(conn: sqlite3.Connection) -> None:
    result = ingest_file(conn, "tests/fixtures/nope.pdf", project_id="ak")
    assert result.ok is False
    assert result.error_kind == "parse"
