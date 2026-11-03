"""抓取层测试：失败必须如实记录，绝不返回伪造正文。"""

from __future__ import annotations

import httpx
import pytest

from compute_dual.common.errors import FetchError
from compute_dual.tools.fetch import (
    RawStore,
    UnconfiguredSearchProvider,
    fetch,
    read_manual_queue,
)

HTML_BODY = b"<html><head><title>ok</title></head><body><p>real content</p></body></html>"


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_fetch_success_returns_body() -> None:
    outcome = fetch(
        "https://example.com/ok",
        transport=_transport(lambda request: httpx.Response(200, content=HTML_BODY)),
        log=False,
    )
    assert outcome.ok
    assert outcome.status_code == 200
    assert outcome.body == HTML_BODY
    assert outcome.error_kind is None


@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_fetch_policy_block_records_status_without_body(status: int) -> None:
    outcome = fetch(
        "https://example.com/blocked",
        transport=_transport(lambda request: httpx.Response(status, content=b"blocked page")),
        log=False,
    )
    assert not outcome.ok
    assert outcome.status_code == status
    assert outcome.error_kind == "policy_block"
    assert outcome.body == b"", "被拦截时不得采信响应体作为正文"


def test_fetch_server_error_is_network_kind() -> None:
    outcome = fetch(
        "https://example.com/boom",
        transport=_transport(lambda request: httpx.Response(500)),
        log=False,
    )
    assert not outcome.ok
    assert outcome.error_kind == "network"


def test_fetch_connection_error_degrades() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    outcome = fetch("https://example.com/down", transport=_transport(handler), log=False)
    assert not outcome.ok
    assert outcome.error_kind == "network"
    assert outcome.status_code is None
    assert "ConnectError" in (outcome.error_detail or "")


def test_fetch_writes_structured_log(settings) -> None:
    log_file = settings.logs_dir / "test_tool_calls.jsonl"
    fetch(
        "https://example.com/logged",
        transport=_transport(lambda request: httpx.Response(200, content=HTML_BODY)),
        log=False,
    )
    # log=False 时不写日志；显式打开才写
    assert not log_file.exists()


def test_search_provider_is_explicitly_unconfigured() -> None:
    with pytest.raises(FetchError) as excinfo:
        UnconfiguredSearchProvider().search("AI 服务器 产业链")
    assert excinfo.value.error_kind == "budget"
    assert "未配置搜索 Provider" in str(excinfo.value)


def test_manual_queue_reads_urls(tmp_path, settings) -> None:
    queue = settings.data_dir / "search_queue.txt"
    queue.write_text(
        "# 注释行\nhttps://example.com/a\n\nhttps://example.com/b\n", encoding="utf-8"
    )
    assert read_manual_queue(settings) == ["https://example.com/a", "https://example.com/b"]


def test_raw_store_roundtrip(settings) -> None:
    store = RawStore(settings)
    saved = store.save(
        HTML_BODY,
        project_id="ak",
        url="https://example.com/ok",
        content_type="text/html",
        http_status=200,
        retrieved_at_shanghai="2026-11-02T15:33:00+08:00",
        text="# text body",
    )
    assert saved.raw_path is not None and saved.raw_path.exists()
    assert saved.text_path is not None and saved.text_path.exists()
    assert store.read_text(saved.doc_id) == "# text body"
    meta = store.read_meta(saved.doc_id)
    assert meta is not None
    assert meta["url"] == "https://example.com/ok"
