"""供需 CSV 读写。

两类档案，字段来自 B 侧公开档案模板 + 后续开发计划 §9 的硬过滤维度
（卡型 / 地区 / 交付周 / 合规标签）。

**只用公开字段**：本层不含联系人、精确报价 —— 那些属于 L2，见 ``compute_dual.private``。

列名容错：同一语义接受多个列名（例如 ``serves_regions`` 也接受 registry 的
``overseas_regions``），缺列即视为「未披露」，不做默认值填充，避免凭空造规格。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

#: 供应侧列别名（左为规范名，右为可接受的其它写法）
SUPPLIER_ALIASES: dict[str, tuple[str, ...]] = {
    "supplier_id": ("id", "supplier_id", "供应商id"),
    "name": ("name", "名称", "主体名称"),
    "home_region": ("region", "home_region", "所在地域"),
    "serves_regions": ("serves_regions", "overseas_regions", "可服务海外区域"),
    "gpu_models": ("gpu_models", "gpu", "卡型", "加速卡型号"),
    "gpu_quantity": ("gpu_quantity", "gpus", "可供卡数"),
    "max_delivery_weeks": ("max_delivery_weeks", "delivery_weeks", "交付周"),
    "compliance_tags": ("compliance_tags", "compliance", "合规标签"),
    "status": ("status", "状态"),
    "evidence_ids": ("evidence_ids", "证据id", "关联证据id"),
    "notes": ("notes", "备注"),
}

DEMAND_ALIASES: dict[str, tuple[str, ...]] = {
    "demand_id": ("id", "demand_id", "需求id"),
    "name": ("name", "名称", "需求方名称"),
    "country": ("country", "国家", "所在国家/地区"),
    "target_regions": ("target_regions", "target_region", "目标区域"),
    "required_gpu_models": ("required_gpu_models", "gpu_models", "gpu", "卡型"),
    "required_gpu_quantity": ("required_gpu_quantity", "gpu_quantity", "需求卡数"),
    "max_delivery_weeks": ("max_delivery_weeks", "delivery_weeks", "交付周"),
    "required_compliance_tags": ("required_compliance_tags", "compliance", "合规标签"),
    "budget_tier": ("budget_tier", "预算量级"),
    "status": ("status", "状态"),
    "evidence_ids": ("evidence_ids", "证据id"),
    "notes": ("notes", "备注"),
}

MULTI_VALUE_SEPARATORS = ("|", "；", ";", ",", "，", " ")

#: 视为「未披露」的占位写法（不得当成真实标签使用）
EMPTY_PLACEHOLDERS = {"无", "暂无", "—", "-", "n/a", "na", "none", "未知", "未披露"}


def _split_multi(value: str | None) -> list[str]:
    """拆分多值字段（``东南亚|中东`` / ``A;B`` / ``甲 乙``）。"""
    if not value:
        return []
    text = value.strip()
    if not text or text.lower() in EMPTY_PLACEHOLDERS:
        return []
    for separator in MULTI_VALUE_SEPARATORS:
        if separator in text:
            parts = [chunk.strip() for chunk in text.split(separator)]
            return [part for part in parts if part and part.lower() not in EMPTY_PLACEHOLDERS]
    return [text]


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _norm(text: str | None) -> str:
    return (text or "").strip()


@dataclass
class Supplier:
    supplier_id: str
    name: str = ""
    home_region: str = ""
    serves_regions: list[str] = field(default_factory=list)
    gpu_models: list[str] = field(default_factory=list)
    gpu_quantity: int | None = None
    max_delivery_weeks: int | None = None
    compliance_tags: list[str] = field(default_factory=list)
    status: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def unknown_fields(self) -> list[str]:
        """返回未披露的关键规格（用于「无法判定」而非猜测）。"""
        unknown = []
        if not self.serves_regions:
            unknown.append("可服务区域")
        if not self.gpu_models:
            unknown.append("卡型")
        if self.max_delivery_weeks is None:
            unknown.append("交付周")
        if self.gpu_quantity is None:
            unknown.append("可供卡数")
        return unknown


@dataclass
class Demand:
    demand_id: str
    name: str = ""
    country: str = ""
    target_regions: list[str] = field(default_factory=list)
    required_gpu_models: list[str] = field(default_factory=list)
    required_gpu_quantity: int | None = None
    max_delivery_weeks: int | None = None
    required_compliance_tags: list[str] = field(default_factory=list)
    budget_tier: str = ""
    status: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    notes: str = ""

    def unknown_fields(self) -> list[str]:
        unknown = []
        if not self.target_regions:
            unknown.append("目标区域")
        if not self.required_gpu_models:
            unknown.append("要求卡型")
        if self.max_delivery_weeks is None:
            unknown.append("可接受交付周")
        return unknown


def _canonical_row(row: dict[str, str], aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """把原始列名映射为规范名；同义列先出现者优先，不覆盖已取值。"""
    lookup = {_norm(key).lower(): value for key, value in row.items() if key}
    resolved: dict[str, str] = {}
    for canonical, candidates in aliases.items():
        for candidate in candidates:
            key = candidate.lower()
            if key in lookup and _norm(lookup[key]):
                resolved[canonical] = _norm(lookup[key])
                break
    return resolved


def load_suppliers(path: str | Path) -> list[Supplier]:
    """读取供应侧 CSV。"""
    rows = _read_csv(path)
    suppliers: list[Supplier] = []
    for raw in rows:
        data = _canonical_row(raw, SUPPLIER_ALIASES)
        entity_id = data.get("supplier_id") or data.get("name")
        if not entity_id:
            continue
        suppliers.append(
            Supplier(
                supplier_id=entity_id,
                name=data.get("name", entity_id),
                home_region=data.get("home_region", ""),
                serves_regions=_split_multi(data.get("serves_regions")),
                gpu_models=[m.upper() for m in _split_multi(data.get("gpu_models"))],
                gpu_quantity=_to_int(data.get("gpu_quantity")),
                max_delivery_weeks=_to_int(data.get("max_delivery_weeks")),
                compliance_tags=_split_multi(data.get("compliance_tags")),
                status=data.get("status", ""),
                evidence_ids=_split_multi(data.get("evidence_ids")),
                notes=data.get("notes", ""),
            )
        )
    return suppliers


def load_demands(path: str | Path) -> list[Demand]:
    """读取需求侧 CSV。"""
    rows = _read_csv(path)
    demands: list[Demand] = []
    for raw in rows:
        data = _canonical_row(raw, DEMAND_ALIASES)
        entity_id = data.get("demand_id") or data.get("name")
        if not entity_id:
            continue
        demands.append(
            Demand(
                demand_id=entity_id,
                name=data.get("name", entity_id),
                country=data.get("country", ""),
                target_regions=_split_multi(data.get("target_regions")),
                required_gpu_models=[
                    m.upper() for m in _split_multi(data.get("required_gpu_models"))
                ],
                required_gpu_quantity=_to_int(data.get("required_gpu_quantity")),
                max_delivery_weeks=_to_int(data.get("max_delivery_weeks")),
                required_compliance_tags=_split_multi(data.get("required_compliance_tags")),
                budget_tier=data.get("budget_tier", ""),
                status=data.get("status", ""),
                evidence_ids=_split_multi(data.get("evidence_ids")),
                notes=data.get("notes", ""),
            )
        )
    return demands


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"CSV 不存在：{file_path}")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with file_path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码 CSV：{file_path}（建议存为 UTF-8）")


def write_rows(path: str | Path, fieldnames: list[str], rows: list[dict[str, str]]) -> Path:
    """写 CSV（导出匹配结果用）。"""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return out
