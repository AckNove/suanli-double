"""证据表 markdown 往返测试：用交接包里的真实证据表做导入。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from compute_dual.evidence import db as evdb
from compute_dual.evidence.markdown_io import (
    import_markdown,
    parse_evidence_table,
    render_markdown,
)
from compute_dual.evidence.models import EpistemicTag, VerificationStatus


def test_parse_simple_table() -> None:
    markdown = """
| ID | 结论内容 | 事实/推断/假设 | 来源名称 | 原始链接 | 发布时间 | 获取时间 | 原文或数据摘录 | 来源类型 | 可信度 | 核验状态 | 冲突信息 |
|----|----------|----------------|----------|----------|----------|----------|----------------|----------|--------|----------|----------|
| A-E01 | 结论一 | 事实 | 来源甲 | https://example.com/1 | 2025-01-01 | 2026-11-02 | 摘录一 | 报告 | 高 | 已对照 | 无 |
| A-E02 | 结论二 | 事实（机构估计） | 来源乙 | 同上 | 2025-01-02 | 2026-11-02 | 摘录二 | 报告 | 中 | 未核验（单源） | 无 |
| A-E03 | 缺口占位 | 【缺失】 | — | — | — | 2026-11-02 | — | — | — | 缺失 | 无 |
"""
    claims, skipped = parse_evidence_table(markdown)

    assert skipped == []
    assert len(claims) == 3

    first, second, third = claims
    assert first.url == "https://example.com/1"
    assert first.verification_status is VerificationStatus.CROSS_CHECKED
    assert second.url == "https://example.com/1", "「同上」应沿用上一行已解析 URL"
    assert second.epistemic_note == "机构估计"
    assert second.verification_note == "未核验（单源）"
    assert third.epistemic_tag is EpistemicTag.MISSING
    assert third.url is None


def test_parse_rejects_malformed_row() -> None:
    markdown = """
| ID | 结论内容 | 事实/推断/假设 | 来源名称 | 原始链接 | 发布时间 | 获取时间 | 原文或数据摘录 | 来源类型 | 可信度 | 核验状态 | 冲突信息 |
|----|----------|----------------|----------|----------|----------|----------|----------------|----------|--------|----------|----------|
| A-E01 | 结论 | 事实 | 来源 | https://example.com/1 | 2025-01-01 | 2026-11-02 |
"""
    claims, skipped = parse_evidence_table(markdown)
    assert claims == []
    assert len(skipped) == 1
    assert "列数不足" in skipped[0].reason


def test_parse_rejects_fact_without_url_but_reports() -> None:
    markdown = """
| ID | 结论内容 | 事实/推断/假设 | 来源名称 | 原始链接 | 发布时间 | 获取时间 | 原文或数据摘录 | 来源类型 | 可信度 | 核验状态 | 冲突信息 |
|----|----------|----------------|----------|----------|----------|----------|----------------|----------|--------|----------|----------|
| A-E01 | 结论 | 事实 | 来源 | — | 2025-01-01 | 2026-11-02 | 摘录 | 报告 | 高 | 未核验 | 无 |
"""
    claims, skipped = parse_evidence_table(markdown)
    assert claims == []
    assert len(skipped) == 1
    assert "闸门" in skipped[0].reason


def test_multi_url_split() -> None:
    markdown = """
| ID | 结论内容 | 事实/推断/假设 | 来源名称 | 原始链接 | 发布时间 | 获取时间 | 原文或数据摘录 | 来源类型 | 可信度 | 核验状态 | 冲突信息 |
|----|----------|----------------|----------|----------|----------|----------|----------------|----------|--------|----------|----------|
| B-E01 | 结论 | 事实 | 多源 | https://a.example.com/1；https://b.example.com/2 | 2025-01-01 | 2026-11-02 | 摘录 | 报告 | 高 | 未核验 | 无 |
"""
    claims, _ = parse_evidence_table(markdown)
    assert claims[0].url == "https://a.example.com/1"
    assert claims[0].extra_urls == "https://b.example.com/2"
    assert len(claims[0].all_urls) == 2


def test_import_real_project_a_table(conn: sqlite3.Connection, handoff_dir: Path) -> None:
    """导入交接包里 A 项目的真实证据表。"""
    path = handoff_dir / "A-算力之王AK" / "证据表.md"
    report = import_markdown(conn, path)

    assert report.inserted >= 25, f"应导入至少 25 条证据，实际 {report.inserted}"
    assert report.skipped == [], f"不应有跳过行：{report.skipped}"

    total = conn.execute("SELECT COUNT(*) AS n FROM evidence_claims").fetchone()["n"]
    assert total == report.inserted

    disputed = evdb.list_claims(conn, status=VerificationStatus.DISPUTED)
    assert disputed, "A-E03/A-E04 的市占口径冲突应被识别为 disputed"

    missing = evdb.list_claims(conn, tag=EpistemicTag.MISSING)
    assert missing, "【缺失】占位行应保留为 missing"

    nvidia = evdb.get_claim(conn, "A-E15")
    assert nvidia is not None
    assert "130,497" in (nvidia.excerpt or "")


def test_import_real_project_b_table(conn: sqlite3.Connection, handoff_dir: Path) -> None:
    path = handoff_dir / "B-中国算力出海" / "证据表.md"
    report = import_markdown(conn, path)

    assert report.inserted >= 10
    kinds = {claim.project_id for claim in evdb.list_claims(conn)}
    assert "outbound" in kinds


def test_import_is_idempotent(conn: sqlite3.Connection, handoff_dir: Path) -> None:
    path = handoff_dir / "A-算力之王AK" / "证据表.md"
    first = import_markdown(conn, path)
    second = import_markdown(conn, path)

    assert second.inserted == 0
    assert second.updated == first.inserted


def test_roundtrip_export_reimport(conn: sqlite3.Connection, handoff_dir: Path) -> None:
    path = handoff_dir / "A-算力之王AK" / "证据表.md"
    import_markdown(conn, path)
    claims = evdb.list_claims(conn, project_id="ak")

    markdown = render_markdown(claims)
    reparsed, skipped = parse_evidence_table(markdown)

    assert skipped == []
    assert len(reparsed) == len(claims)
    assert {c.claim_id for c in reparsed} == {c.claim_id for c in claims}

    original = {c.claim_id: c for c in claims}
    for claim in reparsed:
        source = original[claim.claim_id]
        assert claim.statement == source.statement
        assert claim.epistemic_tag == source.epistemic_tag
        assert claim.url == source.url
