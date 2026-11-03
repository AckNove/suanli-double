"""匹配报告与澄清清单生成。

输出对齐 `03-匹配与解释/_模板.md`：脱敏、无联系方式、无报价数字。
「无法判定」必须显式列出缺口，不许用猜测填满。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ..common.timeutil import now_shanghai_str
from .hardfilter import (
    STATUS_LABELS,
    STATUS_MATCH,
    STATUS_NO_MATCH,
    STATUS_UNDETERMINED,
    MatchResult,
)


def render_match_record(
    result: MatchResult,
    *,
    match_id: str = "MATCH-（候选）",
    creator: str = "compute-dual CLI",
) -> str:
    """单条匹配记录（对应 03 目录模板）。"""
    lines = [
        f"# 匹配记录 ID：{match_id}",
        "",
        "> **脱敏版**：由 `ak match` 自动生成；不含联系方式与报价数字。",
        "> 涉密撮合细节（未公开意向、排他、报价对照）→ `99-敏感商务_仅本项目/MATCH-…_sensitive.md`。",
        "",
        "## 配对",
        f"- 供应：{result.supplier.supplier_id}（{result.supplier.name}）",
        f"- 需求：{result.demand.demand_id}（{result.demand.name}）",
        f"- 状态：{STATUS_LABELS[result.status]}",
        f"- 匹配分数（主观 1–5）：{result.display_score}（算法分 {result.score:g}/100）",
        f"- 更新日期：{date.today().isoformat()}",
        "",
        "## 解释（可核验、不含联系方式与报价数字）",
    ]

    if result.matched_reasons:
        lines.append("- 匹配理由：")
        for index, reason in enumerate(result.matched_reasons, start=1):
            lines.append(f"  {index}. {reason}")
    else:
        lines.append("- 匹配理由：无（未通过任何硬条件）")

    if result.hard_failures:
        lines.append("- 不匹配原因：")
        for failure in result.hard_failures:
            lines.append(f"  - **{failure.label}** `{failure.code.value}`：{failure.detail}")
    else:
        lines.append("- 不匹配原因：无")

    lines.append("- 主要差距 / 风险：")
    if result.gaps:
        for gap in result.gaps:
            lines.append(f"  - {gap}")
    else:
        lines.append("  - 无记录缺口（仍须商务核验）")

    if result.soft_factors:
        lines.append("- 软性加分项：")
        for factor in result.soft_factors:
            detail = f"（{factor.detail}）" if factor.detail else ""
            lines.append(f"  - {factor.label} +{factor.delta:g}{detail}")

    lines.extend(
        [
            f"- 建议下一步：{result.next_step}",
            "",
            "## 敏感指针",
            "- 涉密附件：无（本层不接触 L2 数据）",
            "",
            "## 元数据",
            f"- 创建人：{creator}",
            f"- 创建日期：{now_shanghai_str()}（Asia/Shanghai）",
            f"- 证据ID（供方）：{', '.join(result.supplier.evidence_ids) or '无'}",
            f"- 证据ID（需求）：{', '.join(result.demand.evidence_ids) or '无'}",
            "",
        ]
    )
    return "\n".join(lines)


def render_summary(results: list[MatchResult], *, demand_label: str | None = None) -> str:
    """一份需求的全部候选汇总（人读表格 + 不匹配原因清单）。"""
    header = f"# 匹配汇总：{demand_label}" if demand_label else "# 匹配汇总"
    lines = [
        header,
        "",
        f"> 生成时间：{now_shanghai_str()}（Asia/Shanghai）｜脱敏：无联系人、无报价",
        "> 结论口径：硬条件确定不满足 → 不可匹配；关键规格未披露 → 无法判定（不猜）。",
        "",
        "| 供方 | 名称 | 结论 | 算法分 | 主观分 | 不匹配原因 | 缺口数 |",
        "|------|------|------|--------|--------|------------|--------|",
    ]

    for result in results:
        reasons = "；".join(f"{f.label}({f.code.value})" for f in result.hard_failures) or "—"
        lines.append(
            f"| {result.supplier.supplier_id} | {result.supplier.name} | "
            f"{STATUS_LABELS[result.status]} | {result.score:g} | {result.display_score} | "
            f"{reasons} | {len(result.gaps)} |"
        )

    counts = {
        STATUS_MATCH: sum(1 for r in results if r.status == STATUS_MATCH),
        STATUS_UNDETERMINED: sum(1 for r in results if r.status == STATUS_UNDETERMINED),
        STATUS_NO_MATCH: sum(1 for r in results if r.status == STATUS_NO_MATCH),
    }
    lines.extend(
        [
            "",
            f"- 可匹配：{counts[STATUS_MATCH]}｜无法判定：{counts[STATUS_UNDETERMINED]}｜不可匹配：{counts[STATUS_NO_MATCH]}",
            "",
        ]
    )

    undetermined = [r for r in results if r.status == STATUS_UNDETERMINED]
    if undetermined:
        lines.append("## 无法判定的缺口（补不齐不要外联）")
        lines.append("")
        for result in undetermined:
            lines.append(f"- {result.supplier.supplier_id}（{result.supplier.name}）：")
            for gap in result.gaps:
                lines.append(f"  - {gap}")
        lines.append("")

    mismatched = [r for r in results if r.status == STATUS_NO_MATCH]
    if mismatched:
        lines.append("## 不匹配原因")
        lines.append("")
        for result in mismatched:
            lines.append(f"- {result.supplier.supplier_id}（{result.supplier.name}）：")
            for reason in result.mismatch_reasons():
                lines.append(f"  - {reason}")
        lines.append("")

    return "\n".join(lines)


CLARIFY_TEMPLATE = """# 商务澄清清单：{demand_id} × {supplier_id}

> 用途：向供需方补齐**硬过滤所需字段**。禁止在未获 Arthur 确认前外发。
> 敏感联系方式一律走 `99-敏感商务_仅本项目/`，本清单只列问题。

## 待补规格（来自系统判定的缺口）

{gaps}

## 通用必问项
1. 可服务的海外区域与对应机房城市（是否含跨境专线与数据驻留方案）
2. 可售卡型与数量区间（含是否为最新一代、是否混插）
3. 交付周期：从签约到可用的周数，含扩容节奏
4. 合规：出口管制适配、数据驻留、认证（等保/ISO 等）
5. 商务形态：裸金属 / 托管 / 云 API / 整机柜；最小起订量级

## 记录要求
- 通话/邮件要点写入 `04-商务澄清与跟进/_跟进日志.md`（脱敏）
- 精确报价与联系人入 `99-敏感商务_仅本项目/`
"""


def render_clarify(result: MatchResult) -> str:
    """生成澄清清单（问题清单，不含答案）。"""
    gap_lines = (
        "\n".join(f"- {gap}" for gap in result.gaps)
        if result.gaps
        else "- 无结构性缺口（仍建议逐项复核公开口径）"
    )
    return CLARIFY_TEMPLATE.format(
        demand_id=result.demand.demand_id,
        supplier_id=result.supplier.supplier_id,
        gaps=gap_lines,
    )


def write_text(path: str | Path, content: str) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    return out
