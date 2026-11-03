"""证据模型与硬闸门测试（T12 的核心不变量）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from compute_dual.common.timeutil import now_shanghai_str
from compute_dual.evidence.models import (
    EpistemicTag,
    EvidenceClaim,
    VerificationStatus,
    coerce_epistemic_note,
    coerce_status,
    coerce_tag,
)


def make_claim(**overrides):
    payload = {
        "claim_id": "A-E01",
        "project_id": "ak",
        "statement": "结论",
        "epistemic_tag": "fact",
        "url": "https://example.com/a",
        "retrieved_at_shanghai": now_shanghai_str(),
        "excerpt": "摘录",
    }
    payload.update(overrides)
    return EvidenceClaim(**payload)


def test_fact_without_url_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_claim(url=None)
    assert "闸门" in str(excinfo.value)


def test_fact_with_relative_url_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_claim(url="example.com/a")
    assert "http" in str(excinfo.value)


def test_missing_tag_may_omit_url() -> None:
    claim = make_claim(epistemic_tag="missing", url=None, excerpt=None)
    assert claim.epistemic_tag is EpistemicTag.MISSING
    assert claim.url is None


def test_inference_may_omit_url() -> None:
    claim = make_claim(epistemic_tag="inference", url=None)
    assert claim.epistemic_tag is EpistemicTag.INFERENCE


def test_same_as_placeholder_url_becomes_none() -> None:
    claim = make_claim(epistemic_tag="inference", url="同上")
    assert claim.url is None


def test_claim_id_format_enforced() -> None:
    with pytest.raises(ValidationError):
        make_claim(claim_id="E01")


def test_retrieved_at_converted_to_shanghai() -> None:
    claim = make_claim(retrieved_at_shanghai="2026-11-02T00:00:00+00:00")
    assert claim.retrieved_at_shanghai.endswith("+08:00")


def test_published_at_keeps_vague_text() -> None:
    claim = make_claim(published_at="页面标注2026相关")
    assert claim.published_at == "页面标注2026相关"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("事实", EpistemicTag.FACT),
        ("事实（机构估计·转引）", EpistemicTag.FACT),
        ("假设（二手定量）", EpistemicTag.HYPOTHESIS),
        ("推断", EpistemicTag.INFERENCE),
        ("【缺失】", EpistemicTag.MISSING),
        ("fact", EpistemicTag.FACT),
    ],
)
def test_coerce_tag(raw: str, expected: EpistemicTag) -> None:
    assert coerce_tag(raw) is expected


def test_coerce_tag_invalid() -> None:
    with pytest.raises(ValueError):
        coerce_tag("玄学")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("已打开/互引一致", VerificationStatus.CROSS_CHECKED),
        ("已打开", VerificationStatus.UNVERIFIED),
        ("部分核验", VerificationStatus.UNVERIFIED),
        ("冲突待消（原文已复核）", VerificationStatus.DISPUTED),
        ("已对照（PDF原文）", VerificationStatus.CROSS_CHECKED),
        ("", VerificationStatus.UNVERIFIED),
    ],
)
def test_coerce_status_prefers_longest_prefix(raw: str, expected: VerificationStatus) -> None:
    assert coerce_status(raw) is expected


def test_coerce_epistemic_note() -> None:
    assert coerce_epistemic_note("事实（机构估计·转引）") == "机构估计·转引"
    assert coerce_epistemic_note("事实") is None


def test_transition_rules() -> None:
    claim = make_claim()
    claim.assert_can_transition(VerificationStatus.CROSS_CHECKED)
    claim.assert_can_transition(VerificationStatus.REJECTED)

    rejected = make_claim(verification_status="rejected")
    with pytest.raises(Exception):
        rejected.assert_can_transition(VerificationStatus.UNVERIFIED)


def test_all_urls_includes_extra() -> None:
    claim = make_claim(extra_urls="https://example.com/b;https://example.com/c")
    assert claim.all_urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]


def test_citation_format() -> None:
    assert make_claim().citation == "[@A-E01]"
