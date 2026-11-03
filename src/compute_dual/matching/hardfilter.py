"""硬过滤与「不匹配原因」生成（后续开发计划 §9）。

顺序：先判硬条件（卡型 / 地区 / 交付周 / 合规标签 / 容量 / 供方状态），
再算软性得分。任一条硬条件**确定不满足**即 ``no_match``；
关键字段未披露则 ``undetermined`` —— 不猜、不补默认值。

输出里不含联系人、电话、邮箱、报价数字（这些属 L2，不在本层数据模型里）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .csvio import Demand, Supplier

#: 供方状态里表示「不活跃」的写法（命中即硬排除）
INACTIVE_STATUS_MARKERS = ("搁置", "暂停", "停止", "已否决", "失效", "下线")

#: 成熟状态（仅影响软性得分，不作为硬条件）
MATURE_STATUS_MARKERS = ("可报价", "已合作", "报价中")
CONTACTED_STATUS_MARKERS = ("已接触", "评估中")


class ReasonCode(str, Enum):
    """硬过滤失败原因码（稳定值，供下游统计）。"""

    REGION_MISMATCH = "REGION_MISMATCH"
    GPU_MODEL_UNAVAILABLE = "GPU_MODEL_UNAVAILABLE"
    DELIVERY_TOO_SLOW = "DELIVERY_TOO_SLOW"
    CAPACITY_INSUFFICIENT = "CAPACITY_INSUFFICIENT"
    COMPLIANCE_GAP = "COMPLIANCE_GAP"
    SUPPLIER_INACTIVE = "SUPPLIER_INACTIVE"


REASON_LABELS: dict[ReasonCode, str] = {
    ReasonCode.REGION_MISMATCH: "区域不匹配",
    ReasonCode.GPU_MODEL_UNAVAILABLE: "无所需卡型",
    ReasonCode.DELIVERY_TOO_SLOW: "交付周期过长",
    ReasonCode.CAPACITY_INSUFFICIENT: "可售容量不足",
    ReasonCode.COMPLIANCE_GAP: "合规标签缺失",
    ReasonCode.SUPPLIER_INACTIVE: "供方状态不活跃",
}

STATUS_MATCH = "match"
STATUS_NO_MATCH = "no_match"
STATUS_UNDETERMINED = "undetermined"

STATUS_LABELS = {
    STATUS_MATCH: "可匹配（硬条件全部通过）",
    STATUS_NO_MATCH: "不可匹配（硬条件不通过）",
    STATUS_UNDETERMINED: "无法判定（关键规格未披露）",
}


@dataclass
class HardFailure:
    code: ReasonCode
    detail: str

    @property
    def label(self) -> str:
        return REASON_LABELS[self.code]

    @property
    def line(self) -> str:
        return f"[{self.code.value}] {self.label}：{self.detail}"


@dataclass
class SoftFactor:
    label: str
    delta: float
    detail: str = ""


@dataclass
class MatchResult:
    supplier: Supplier
    demand: Demand
    status: str
    score: float
    hard_failures: list[HardFailure] = field(default_factory=list)
    soft_factors: list[SoftFactor] = field(default_factory=list)
    matched_reasons: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    @property
    def subjective_score(self) -> float | None:
        """模板里的「匹配分数（主观 1–5）」：由 0–100 折算，仅供人读。

        「无法判定」不给分 —— 未披露规格的配对谈不上匹配度，给中等分会误导。
        """
        if self.status != STATUS_MATCH:
            return None
        return round(max(1.0, self.score / 20.0), 1)

    @property
    def display_score(self) -> str:
        return "—" if self.subjective_score is None else str(self.subjective_score)

    @property
    def next_step(self) -> str:
        if self.status == STATUS_MATCH:
            return "出澄清清单（ak match clarify 模板）→ 待 Arthur 确认后再商务试探"
        if self.status == STATUS_UNDETERMINED:
            return "先补规格缺口（区域/卡型/交付周/容量），补不齐不要外联"
        return "暂不推进；如供需方再次披露规格，可重新评估"

    def mismatch_reasons(self) -> list[str]:
        """人读的「不匹配原因」列表。"""
        return [failure.line for failure in self.hard_failures]


# --------------------------------------------------------------------------- 判定工具


def _contains_any(haystack: list[str], needle: str) -> bool:
    target = (needle or "").strip().lower()
    if not target:
        return False
    return any(target in item.strip().lower() or item.strip().lower() in target for item in haystack)


def _intersect(demand_values: list[str], supplier_values: list[str]) -> list[str]:
    """双向子串匹配取交集（容忍「昇腾910B」与「910B」这类写法差异）。"""
    hits: list[str] = []
    for value in demand_values:
        if _contains_any(supplier_values, value):
            hits.append(value)
    return hits


def _missing_required(required: list[str], available: list[str]) -> list[str]:
    return [item for item in required if not _contains_any(available, item)]


def _is_inactive(status: str) -> bool:
    text = (status or "").strip()
    return any(marker in text for marker in INACTIVE_STATUS_MARKERS)


# --------------------------------------------------------------------------- 主流程


def match_one(demand: Demand, supplier: Supplier) -> MatchResult:
    """对单个供需对做硬过滤 + 打分。"""
    failures: list[HardFailure] = []
    matched: list[str] = []
    gaps: list[str] = []
    factors: list[SoftFactor] = []

    # 1) 供方状态
    if _is_inactive(supplier.status):
        failures.append(
            HardFailure(
                ReasonCode.SUPPLIER_INACTIVE,
                f"供方状态为「{supplier.status}」，不可用于撮合",
            )
        )
    elif supplier.status:
        matched.append(f"供方状态「{supplier.status}」可推进")

    # 2) 区域
    if not demand.target_regions:
        gaps.append("需求侧未披露目标区域")
    if not supplier.serves_regions:
        gaps.append("供应侧未披露可服务海外区域")
    if demand.target_regions and supplier.serves_regions:
        overlap = _intersect(demand.target_regions, supplier.serves_regions)
        if overlap:
            matched.append(
                f"区域可覆盖：需求 {','.join(demand.target_regions)} ∩ 供方可服务 {','.join(supplier.serves_regions)} → {overlap[0]}"
            )
        else:
            failures.append(
                HardFailure(
                    ReasonCode.REGION_MISMATCH,
                    f"需求目标区域 {','.join(demand.target_regions)} 与供方可服务区域 {','.join(supplier.serves_regions)} 无交集",
                )
            )

    # 3) 卡型
    if not demand.required_gpu_models:
        gaps.append("需求侧未披露所需卡型")
    if not supplier.gpu_models:
        gaps.append("供应侧未披露可供卡型")
    if demand.required_gpu_models and supplier.gpu_models:
        overlap = _intersect(demand.required_gpu_models, supplier.gpu_models)
        if overlap:
            matched.append(f"卡型可满足：{overlap[0]}")
        else:
            failures.append(
                HardFailure(
                    ReasonCode.GPU_MODEL_UNAVAILABLE,
                    f"需求要求 {','.join(demand.required_gpu_models)}，供方仅有 {','.join(supplier.gpu_models)}",
                )
            )

    # 4) 交付周
    if demand.max_delivery_weeks is None:
        gaps.append("需求侧未披露可接受交付周期")
    if supplier.max_delivery_weeks is None:
        gaps.append("供应侧未披露交付周期")
    if demand.max_delivery_weeks is not None and supplier.max_delivery_weeks is not None:
        if supplier.max_delivery_weeks > demand.max_delivery_weeks:
            failures.append(
                HardFailure(
                    ReasonCode.DELIVERY_TOO_SLOW,
                    f"供方需 {supplier.max_delivery_weeks} 周，需求上限 {demand.max_delivery_weeks} 周",
                )
            )
        else:
            margin = demand.max_delivery_weeks - supplier.max_delivery_weeks
            matched.append(f"交付周期满足（余量 {margin} 周）")
            if margin >= 8:
                factors.append(SoftFactor("交付余量充裕", 15, f"{margin} 周"))
            elif margin >= 4:
                factors.append(SoftFactor("交付余量中等", 10, f"{margin} 周"))
            else:
                factors.append(SoftFactor("交付余量偏紧", 5, f"{margin} 周"))

    # 5) 容量
    if demand.required_gpu_quantity is None:
        gaps.append("需求侧未披露卡数规模")
    if supplier.gpu_quantity is None:
        gaps.append("供应侧未披露可供卡数")
    if demand.required_gpu_quantity is not None and supplier.gpu_quantity is not None:
        if supplier.gpu_quantity < demand.required_gpu_quantity:
            failures.append(
                HardFailure(
                    ReasonCode.CAPACITY_INSUFFICIENT,
                    f"供方可供 {supplier.gpu_quantity} 卡，需求 {demand.required_gpu_quantity} 卡",
                )
            )
        else:
            ratio = supplier.gpu_quantity / max(demand.required_gpu_quantity, 1)
            matched.append(
                f"容量满足（{supplier.gpu_quantity} ≥ {demand.required_gpu_quantity}，{ratio:.1f}x）"
            )
            if ratio >= 2:
                factors.append(SoftFactor("容量冗余充足", 10, f"{ratio:.1f}x"))
            elif ratio >= 1.2:
                factors.append(SoftFactor("容量有余量", 6, f"{ratio:.1f}x"))
            else:
                factors.append(SoftFactor("容量刚好够", 3, f"{ratio:.1f}x"))

    # 6) 合规标签
    if demand.required_compliance_tags:
        if not supplier.compliance_tags:
            # 「未披露」不等于「不具备」：列入缺口，不据此判死
            gaps.append("供应侧未披露任何合规标签（需求要求必备合规项）")
        else:
            missing = _missing_required(demand.required_compliance_tags, supplier.compliance_tags)
            if missing:
                failures.append(
                    HardFailure(
                        ReasonCode.COMPLIANCE_GAP,
                        f"缺少必备合规标签：{','.join(missing)}（供方现有：{','.join(supplier.compliance_tags)}）",
                    )
                )
            else:
                matched.append(f"合规标签齐备：{','.join(demand.required_compliance_tags)}")

    # 7) 证据充分度（软性）
    evidence_total = len(set(demand.evidence_ids) | set(supplier.evidence_ids))
    if evidence_total:
        bonus = min(evidence_total, 3) * 5
        factors.append(SoftFactor("证据覆盖", bonus, f"{evidence_total} 条证据ID"))
    else:
        gaps.append("供需双方均无关联证据ID（结论不可回查）")

    # 8) 状态成熟度（软性）
    if any(marker in (supplier.status or "") for marker in MATURE_STATUS_MARKERS):
        factors.append(SoftFactor("供方已可报价/已合作", 15))
    elif any(marker in (supplier.status or "") for marker in CONTACTED_STATUS_MARKERS):
        factors.append(SoftFactor("供方已接触", 8))
    if any(marker in (demand.status or "") for marker in ("需求明确", "评估中", "已接触")):
        factors.append(SoftFactor("需求已明确", 5))

    # 判定状态
    if failures:
        status = STATUS_NO_MATCH
    elif gaps:
        status = STATUS_UNDETERMINED
    else:
        status = STATUS_MATCH

    score = 0.0
    if status == STATUS_MATCH:
        score = 50.0 + sum(factor.delta for factor in factors)
        score = max(0.0, min(100.0, score))

    return MatchResult(
        supplier=supplier,
        demand=demand,
        status=status,
        score=round(score, 1),
        hard_failures=failures,
        soft_factors=factors,
        matched_reasons=matched,
        gaps=gaps,
    )


def match_demand(demand: Demand, suppliers: list[Supplier]) -> list[MatchResult]:
    """对一份需求跑全部供方，按状态与分数排序。

    排序：可匹配 → 无法判定 → 不可匹配；同状态内分数降序。
    """
    order = {STATUS_MATCH: 0, STATUS_UNDETERMINED: 1, STATUS_NO_MATCH: 2}
    results = [match_one(demand, supplier) for supplier in suppliers]
    return sorted(results, key=lambda r: (order[r.status], -r.score, r.supplier.supplier_id))
