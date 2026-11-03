"""证据表 markdown ↔ 研究库 互转。

交接包里已有两份权威 markdown 证据表（A/B），导入后可立即用 SQL 与 CLI 检索；
导出用于把库内证据重新渲染成人读表格，保证双向一致。

解析失败不抛错（设计 §15 失败降级）：跳过该行并计入 ``ImportReport.skipped``，
由调用方决定是否中止。禁止为「看起来完整」而编造缺失字段。
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..common.ids import CLAIM_ID_RE, normalize_project_id
from ..common.timeutil import to_shanghai_str
from .db import add_claim, get_claim, upsert_claim
from .models import (
    STATUS_LABELS,
    TAG_LABELS,
    EvidenceClaim,
    EpistemicTag,
    VerificationStatus,
    coerce_epistemic_note,
    coerce_status,
    coerce_tag,
    looks_like_status,
)

URL_RE = re.compile(r"https?://[^\s；;，,）)】\]]+")

SAME_AS_MARKERS = ("同上", "同 A-E", "同 B-E", "同A-E", "同B-E", "同 ")

COLUMNS = [
    "ID",
    "结论内容",
    "事实/推断/假设",
    "来源名称",
    "原始链接",
    "发布时间",
    "获取时间",
    "原文或数据摘录",
    "来源类型",
    "可信度",
    "核验状态",
    "冲突信息",
]


@dataclass
class SkippedRow:
    line_no: int
    raw_id: str
    reason: str


@dataclass
class ImportReport:
    source: str
    inserted: int = 0
    updated: int = 0
    skipped: list[SkippedRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + len(self.skipped)


def _project_from_claim_id(claim_id: str) -> str:
    prefix = claim_id.split("-")[0]
    return {"A": "ak", "B": "outbound"}.get(prefix, "shared")


def _split_row(line: str) -> list[str]:
    """按未转义的 ``|`` 拆分表格行。"""
    protected = line.replace("\\|", "\x00")
    cells = [cell.strip().replace("\x00", "|") for cell in protected.strip().strip("|").split("|")]
    return cells


def _resolve_url(cell: str, previous_url: str | None) -> tuple[str | None, str | None]:
    """返回 (首个 URL, 其余 URL 拼串)。``同上`` 沿用上一行已解析 URL，不臆造。"""
    text = (cell or "").strip()
    if not text or text in {"—", "-", "无", "N/A"}:
        return None, None
    if text.startswith(SAME_AS_MARKERS) or text.startswith("同"):
        return previous_url, None

    urls = URL_RE.findall(text)
    if not urls:
        return None, None
    first = urls[0].rstrip("。；;,，")
    rest = ";".join(url.rstrip("。；;,，") for url in urls[1:]) or None
    return first, rest


def parse_evidence_table(markdown: str) -> tuple[list[EvidenceClaim], list[SkippedRow]]:
    """解析 markdown 证据表为 EvidenceClaim 列表（不写库）。"""
    claims: list[EvidenceClaim] = []
    skipped: list[SkippedRow] = []
    previous_url: str | None = None

    for line_no, raw_line in enumerate(markdown.splitlines(), start=1):
        line = raw_line.strip()
        if not line.startswith("|"):
            continue

        cells = _split_row(line)
        if not cells:
            continue
        head = cells[0]
        if head in {"ID", ""} or set(head) <= {"-", ":"}:
            continue
        if not CLAIM_ID_RE.match(head):
            continue

        if len(cells) < len(COLUMNS):
            skipped.append(
                SkippedRow(line_no, head, f"列数不足：期望 {len(COLUMNS)}，实得 {len(cells)}")
            )
            continue

        if len(cells) > len(COLUMNS):
            # 摘录里若有未转义竖线，合并多出的单元格回到摘录列，避免整行丢弃
            extra = len(cells) - len(COLUMNS)
            merged = (
                cells[:7] + [" | ".join(cells[7 : 7 + extra + 1])] + cells[7 + extra + 1 :]
            )
            cells = merged

        (
            claim_id,
            statement,
            tag_cell,
            source_name,
            link_cell,
            published_cell,
            retrieved_cell,
            excerpt,
            source_type,
            trust_level,
            status_cell,
            conflict_cell,
        ) = cells[: len(COLUMNS)]

        url, extra_urls = _resolve_url(link_cell, previous_url)
        if url:
            previous_url = url

        try:
            tag = coerce_tag(tag_cell)
            status = coerce_status(status_cell)
            note_status = (status_cell or "").strip()
            claim = EvidenceClaim(
                claim_id=claim_id,
                project_id=_project_from_claim_id(claim_id),
                statement=statement,
                epistemic_tag=tag,
                source_name=source_name or None,
                url=url,
                published_at=published_cell or None,
                retrieved_at_shanghai=retrieved_cell or "",
                excerpt=excerpt or None,
                source_type=source_type or None,
                trust_level=trust_level or None,
                verification_status=status,
                conflict_notes=conflict_cell or None,
                epistemic_note=coerce_epistemic_note(tag_cell),
                verification_note=note_status if note_status != STATUS_LABELS[status] else None,
                extra_urls=extra_urls,
            )
        except ValueError as exc:
            if "epistemic_tag" in str(exc) and looks_like_status(tag_cell):
                reason = (
                    f"列错位：第 3 列（事实/推断/假设）填的是核验状态「{tag_cell}」，"
                    "无法据此判断认知标签；未擅自推断，请人工改表后重导"
                )
            else:
                reason = f"ValueError: {exc}"
            skipped.append(SkippedRow(line_no, claim_id, reason))
            continue
        except Exception as exc:  # noqa: BLE001 - 单行失败降级处理
            skipped.append(SkippedRow(line_no, claim_id, f"{type(exc).__name__}: {exc}"))
            continue

        claims.append(claim)

    return claims, skipped


def import_markdown(
    conn: sqlite3.Connection,
    markdown_path: str | Path,
    *,
    project_override: str | None = None,
) -> ImportReport:
    """把 markdown 证据表导入研究库（存在则更新可变字段，不覆盖核验状态）。"""
    path = Path(markdown_path)
    text = path.read_text(encoding="utf-8")
    claims, skipped = parse_evidence_table(text)

    report = ImportReport(source=str(path), skipped=list(skipped))
    for claim in claims:
        if project_override:
            claim = claim.model_copy(update={"project_id": normalize_project_id(project_override)})
        exists = get_claim(conn, claim.claim_id) is not None
        if exists:
            upsert_claim(conn, claim)
            report.updated += 1
        else:
            add_claim(conn, claim)
            report.inserted += 1
    return report


# --------------------------------------------------------------------------- 导出


def _render_date(value: str | None) -> str:
    if not value:
        return ""
    if isinstance(value, str) and "T00:00:00+08:00" in value:
        return value.split("T", 1)[0]
    return value


def _render_tag(claim: EvidenceClaim) -> str:
    label = TAG_LABELS[claim.epistemic_tag]
    return f"{label}（{claim.epistemic_note}）" if claim.epistemic_note else label


def _render_status(claim: EvidenceClaim) -> str:
    base = STATUS_LABELS[claim.verification_status]
    note = claim.verification_note
    if note and note != base:
        return note if note.startswith(base) else f"{base}（{note}）"
    return base


def _escape_cell(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def render_markdown(
    claims: list[EvidenceClaim],
    *,
    title: str = "证据表（由 research.db 再生成）",
) -> str:
    """把证据列表渲染为与原始格式一致的 markdown 表格。"""
    from ..common.timeutil import now_shanghai_str

    lines: list[str] = [
        f"# {title}",
        "",
        f"> 生成时间：{now_shanghai_str()}（Asia/Shanghai）",
        "> 本文件由 `ak evidence export-md` 从 `research.db` 再生成；库为准，手改易被覆盖。",
        "> 核验状态机：unverified → cross_checked → disputed → rejected。",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "|".join(["----"] * len(COLUMNS)) + "|",
    ]

    for claim in claims:
        links = claim.url or ""
        if claim.extra_urls:
            links = f"{links}；{claim.extra_urls}" if links else claim.extra_urls
        cells = [
            claim.claim_id,
            claim.statement,
            _render_tag(claim),
            claim.source_name or "",
            links,
            _render_date(claim.published_at),
            _render_date(claim.retrieved_at_shanghai),
            claim.excerpt or "",
            claim.source_type or "",
            claim.trust_level or "",
            _render_status(claim),
            claim.conflict_notes or "",
        ]
        lines.append("| " + " | ".join(_escape_cell(cell) for cell in cells) + " |")

    lines.append("")
    missing = [c.claim_id for c in claims if c.epistemic_tag is EpistemicTag.MISSING]
    disputed = [c.claim_id for c in claims if c.verification_status is VerificationStatus.DISPUTED]
    if missing:
        lines.append(f"- 【缺失】占位：{'、'.join(missing)}")
    if disputed:
        lines.append(f"- 冲突待消：{'、'.join(disputed)}")
    return "\n".join(lines) + "\n"
