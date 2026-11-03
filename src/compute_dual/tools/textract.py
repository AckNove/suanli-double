"""HTML 正文抽取。

优先 ``trafilatura``（去导航/广告，适合新闻与报告页）；不可用时退回标准库
``HTMLParser`` 的朴素抽文本 —— 宁可文本粗糙，也不返回编造内容。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_WS_RE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class _FallbackExtractor(HTMLParser):
    """丢弃 script/style，按块级标签补换行。"""

    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {
        "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        name = tag.lower()
        if name in self.SKIP:
            self._skip_depth += 1
            return
        if name == "title":
            self._in_title = True
        if name in self.BLOCK:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if name == "title":
            self._in_title = False
        if name in self.BLOCK:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
        self._chunks.append(data)

    def text(self) -> str:
        raw = "".join(self._chunks)
        lines = [_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
        return _BLANK_LINES_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def _decode(payload: bytes, encoding_hint: str | None = None) -> str:
    for encoding in filter(None, [encoding_hint, "utf-8", "gb18030", "big5", "latin-1"]):
        try:
            return payload.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def extract_html_text(payload: bytes, *, encoding: str | None = None) -> tuple[str, str | None]:
    """返回 (正文文本, 页面标题)。"""
    html = _decode(payload, encoding)
    if not html.strip():
        return "", None

    try:  # 优先 trafilatura
        import trafilatura  # noqa: PLC0415 - 延迟导入，缺依赖时仍可降级

        extracted = trafilatura.extract(
            html, include_tables=True, include_comments=False, favor_precision=True
        )
        if extracted and extracted.strip():
            title = None
            try:
                meta = trafilatura.extract_metadata(html)
                title = getattr(meta, "title", None) if meta else None
            except Exception:  # noqa: BLE001 - 元数据失败不影响正文
                title = None
            return extracted.strip(), title
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - 抽取器异常退回朴素模式
        pass

    parser = _FallbackExtractor()
    parser.feed(html)
    return parser.text(), parser.title or None


def looks_like_html(payload: bytes, content_type: str | None = None) -> bool:
    if content_type and "html" in content_type.lower():
        return True
    head = payload[:512].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<body" in head
