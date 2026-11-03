"""研究草稿生成（T14 交付物）。

**边界**：本模块只做「证据编排 + 缺口标注 + 引用渲染」，
不生成分析结论、不替人下判断。结论区明确留白并标注需人工/LLM 撰写后再审。

每条引用形如 ``[@A-E09]``，可回查到 ``research.db.evidence_claims``。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..common.timeutil import now_shanghai_str
from ..evidence.models import EvidenceClaim, EpistemicTag, VerificationStatus
from .qa import QaReport, run_qa
from .tickets import Ticket, get_ticket

TAG_LABELS = {
    EpistemicTag.FACT: "事实",
    EpistemicTag.INFERENCE: "推断",
    EpistemicTag.HYPOTHESIS: "假设",
    EpistemicTag.MISSING: "【缺失】",
}

STATUS_LABELS = {
    VerificationStatus.UNVERIFIED: "未核验",
    VerificationStatus.CROSS_CHECKED: "已对照",
    VerificationStatus.DISPUTED: "冲突待消",
    VerificationStatus.REJECTED: "已否决",
}


@dataclass
class DraftResult:
    ticket: Ticket
    markdown: str
    qa: QaReport
    cited_claim_ids: list[str]
    gaps: list[str]


def _render_claim(claim: EvidenceClaim, *, indent: str = "  ") -> list[str]:
    tag = TAG_LABELS[claim.epistemic_tag]
    if claim.epistemic_note:
        tag = f"{tag}（{claim.epistemic_note}）"
    status = STATUS_LABELS[claim.verification_status]
    if claim.verification_note and claim.verification_note != status:
        status = claim.verification_note

    lines = [f"{indent}- {claim.citation} {claim.statement}"]
    lines.append(f"{indent}  - 标签：{tag}｜核验：{status}")
    source_bits = [bit for bit in (claim.source_name, claim.source_type, claim.trust_level) if bit]
    if source_bits:
        lines.append(f"{indent}  - 来源：{'｜'.join(source_bits)}")
    if claim.all_urls:
        lines.append(f"{indent}  - 链接：{claim.all_urls[0]}")
        for extra in claim.all_urls[1:]:
            lines.append(f"{indent}    - 另一来源：{extra}")
    if claim.published_at:
        lines.append(f"{indent}  - 发布：{_render_date(claim.published_at)}")
    if claim.excerpt:
        excerpt = " ".join(claim.excerpt.split())
        if len(excerpt) > 400:
            excerpt = excerpt[:400] + "…"
        lines.append(f"{indent}  - 摘录：{excerpt}")
    if claim.conflict_notes:
        lines.append(f"{indent}  - ⚠ 冲突/注意：{claim.conflict_notes}")
    return lines


def _render_date(value: str) -> str:
    """日期型时间只显示日期部分，避免满屏 T00:00:00+08:00。"""
    if "T00:00:00+08:00" in value:
        return value.split("T", 1)[0]
    return value


def build_draft(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str | None = None,
) -> DraftResult:
    """生成工单草稿（markdown）。"""
    ticket = get_ticket(conn, ticket_id)
    if ticket is None:
        raise ValueError(f"工单不存在：{ticket_id}")

    generated_at = now_shanghai_str()
    cited: list[str] = []
    gaps: list[str] = []
    qa_report = run_qa(conn, ticket.project_id, ticket_id=ticket_id)

    lines: list[str] = [
        f"# {title or ticket.topic} — 研究草稿",
        "",
        "> ⚠ 状态：**自动生成的证据编排稿，未经人工复核，不得对外**。",
        f"> 工单：`{ticket.ticket_id}`｜项目：`{ticket.project_id}`｜生成时间：{generated_at}（Asia/Shanghai）",
    ]
    if ticket.scope:
        lines.append(f"> 范围约束：{ticket.scope}")
    lines.extend(
        [
            ">",
            "> 本稿只做「证据罗列 + 缺口标注」；**结论须人工或 LLM 撰写后再经核验**。",
            "> 引用格式 `[@claim_id]` 可回查 `research.db.evidence_claims`。",
            "",
            "---",
            "",
            "## 1. 研究问题与证据",
            "",
        ]
    )

    if not ticket.questions:
        lines.append("_本工单尚未拆解研究问题。用 `ak ticket ask` 添加。_")
        lines.append("")

    for question in ticket.questions:
        lines.append(f"### Q{question.seq}. {question.question}")
        lines.append("")
        lines.append(f"- 问题 ID：`{question.question_id}`")

        if not question.claims:
            lines.append("- 证据：**无**（数据缺口，不得凭空补写）")
            lines.append("")
            gaps.append(f"{question.question_id}：{question.question}（无任何证据）")
            continue

        missing = [c for c in question.claims if c.epistemic_tag is EpistemicTag.MISSING]
        usable = [c for c in question.claims if c.epistemic_tag is not EpistemicTag.MISSING]
        disputed = [
            c for c in question.claims if c.verification_status is VerificationStatus.DISPUTED
        ]

        lines.append(f"- 证据条数：{len(question.claims)}（可用 {len(usable)}，缺失占位 {len(missing)}）")
        lines.append("")

        for claim in usable:
            lines.extend(_render_claim(claim))
            cited.append(claim.claim_id)
        for claim in missing:
            lines.extend(_render_claim(claim))
            gaps.append(
                f"{claim.claim_id}：{claim.statement}（已被原始研究标记为缺失，需补一手来源）"
            )
        for claim in disputed:
            gaps.append(f"{claim.claim_id}：口径冲突未消 —— {claim.conflict_notes or '见证据表'}")

        if not usable:
            gaps.append(f"{question.question_id}：仅有缺失占位，无可用证据")

        lines.append("")

    lines.extend(
        [
            "## 2. 数据缺口汇总",
            "",
        ]
    )
    if gaps:
        for gap in gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- 无记录缺口（仍需人工抽检）")
    lines.append("")

    lines.extend(
        [
            "## 3. 结论（待人工撰写）",
            "",
            "> 本节**故意留白**：自动流程不产出分析结论，避免把推断当事实。",
            "> 撰写要求：每个判断后附 `[@claim_id]`；无证据的判断必须标「推断/假设」。",
            "",
            "- （待写）",
            "",
            "## 4. 质量自检",
            "",
        ]
    )
    lines.extend(_render_qa_lines(qa_report))

    return DraftResult(
        ticket=ticket,
        markdown="\n".join(lines) + "\n",
        qa=qa_report,
        cited_claim_ids=sorted(set(cited)),
        gaps=gaps,
    )


def _render_qa_lines(report: QaReport) -> list[str]:
    lines = [
        "| 指标 | 值 | 阈值 | 结论 |",
        "|------|----|------|------|",
    ]
    for metric in report.metrics:
        verdict = "通过" if metric.passed else "**不通过**"
        threshold = "" if metric.threshold is None else f"{metric.threshold:g}"
        lines.append(f"| {metric.label} | {metric.display} | {threshold} | {verdict} |")
    lines.append("")
    lines.append(f"- 可对外结论：**{'是' if report.publishable else '否'}**")
    for reason in report.blocking_reasons:
        lines.append(f"  - {reason}")
    return lines


def write_draft(conn: sqlite3.Connection, ticket_id: str, out_path: str | Path, **kwargs) -> DraftResult:
    """生成并写盘。"""
    result = build_draft(conn, ticket_id, **kwargs)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result.markdown, encoding="utf-8")
    return result
