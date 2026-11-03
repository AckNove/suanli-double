"""时区与 ID 工具测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from compute_dual.common.ids import (
    claim_prefix,
    content_doc_id,
    normalize_project_id,
    sequence_suffix,
)
from compute_dual.common.timeutil import (
    SHANGHAI,
    is_shanghai_iso,
    now_shanghai_str,
    parse_shanghai,
    to_shanghai_str,
)


def test_now_shanghai_has_plus8_offset() -> None:
    stamp = now_shanghai_str()
    assert stamp.endswith("+08:00")
    assert is_shanghai_iso(stamp)


def test_parse_shanghai_converts_utc_to_shanghai() -> None:
    converted = parse_shanghai("2026-11-02T00:00:00+00:00")
    assert converted.utcoffset() == timedelta(hours=8)
    assert converted.hour == 8


def test_parse_naive_assumes_shanghai() -> None:
    converted = parse_shanghai("2026-11-02")
    assert converted.tzinfo == SHANGHAI


def test_invalid_time_raises() -> None:
    with pytest.raises(ValueError):
        parse_shanghai("不是时间")


def test_to_shanghai_str_normalizes() -> None:
    moment = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    assert to_shanghai_str(moment) == "2026-11-02T20:00:00+08:00"


def test_is_shanghai_iso_rejects_naive() -> None:
    assert not is_shanghai_iso("2026-11-02T15:33:00")
    assert is_shanghai_iso("2026-11-02T15:33:00+08:00")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("A", "ak"), ("ak", "ak"), ("B", "outbound"), ("出海", "outbound"), ("shared", "shared")],
)
def test_normalize_project_id(raw: str, expected: str) -> None:
    assert normalize_project_id(raw) == expected


def test_normalize_project_id_invalid() -> None:
    with pytest.raises(ValueError):
        normalize_project_id("!!")


def test_claim_prefix_and_sequence() -> None:
    assert claim_prefix("ak") == "A"
    assert claim_prefix("outbound") == "B"
    assert sequence_suffix(1) == "01"
    assert sequence_suffix(13) == "13"
    assert sequence_suffix(100) == "100"


def test_content_doc_id_is_stable_hash() -> None:
    assert content_doc_id(b"abc") == content_doc_id(b"abc")
    assert content_doc_id(b"abc") != content_doc_id(b"abd")
    assert len(content_doc_id(b"abc")) == 16
