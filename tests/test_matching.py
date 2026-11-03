"""供需匹配硬过滤测试（T15 验收：给定样例输出匹配/不匹配原因）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from compute_dual.matching.csvio import load_demands, load_suppliers
from compute_dual.matching.hardfilter import (
    STATUS_MATCH,
    STATUS_NO_MATCH,
    STATUS_UNDETERMINED,
    ReasonCode,
    match_demand,
    match_one,
)
from compute_dual.matching.report import render_clarify, render_match_record, render_summary


@pytest.fixture()
def synthetic(fixtures_dir: Path):
    suppliers = load_suppliers(fixtures_dir / "suppliers_fixture.csv")
    demands = load_demands(fixtures_dir / "demands_fixture.csv")
    return suppliers, demands


def test_csv_loading_reads_multi_values(fixtures_dir: Path) -> None:
    suppliers = load_suppliers(fixtures_dir / "suppliers_fixture.csv")
    alpha = next(s for s in suppliers if s.supplier_id == "SYN-SUP-01")
    assert alpha.serves_regions == ["东南亚", "中东"]
    assert alpha.gpu_models == ["H100", "H200"]
    assert alpha.gpu_quantity == 2000
    assert alpha.max_delivery_weeks == 8
    assert alpha.compliance_tags == ["数据驻留", "出口管制-允许"]


def test_full_match_passes(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-01")

    result = match_one(demand, supplier)
    assert result.status == STATUS_MATCH
    assert result.hard_failures == []
    assert result.score > 50
    assert 1.0 <= result.subjective_score <= 5.0
    assert any("区域可覆盖" in reason for reason in result.matched_reasons)


def test_region_and_model_mismatch_reported(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-02")

    result = match_one(demand, supplier)
    assert result.status == STATUS_NO_MATCH
    codes = {failure.code for failure in result.hard_failures}
    assert ReasonCode.REGION_MISMATCH in codes
    assert ReasonCode.GPU_MODEL_UNAVAILABLE in codes
    assert all(failure.line for failure in result.hard_failures)


def test_capacity_insufficient(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-03")

    result = match_one(demand, supplier)
    assert result.status == STATUS_NO_MATCH
    assert {f.code for f in result.hard_failures} == {ReasonCode.CAPACITY_INSUFFICIENT}


def test_delivery_too_slow(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-04")

    result = match_one(demand, supplier)
    assert result.status == STATUS_NO_MATCH
    assert {f.code for f in result.hard_failures} == {ReasonCode.DELIVERY_TOO_SLOW}


def test_compliance_gap(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-05")

    result = match_one(demand, supplier)
    assert result.status == STATUS_NO_MATCH
    codes = {f.code for f in result.hard_failures}
    assert ReasonCode.COMPLIANCE_GAP in codes


def test_inactive_supplier(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-06")

    result = match_one(demand, supplier)
    assert result.status == STATUS_NO_MATCH
    assert ReasonCode.SUPPLIER_INACTIVE in {f.code for f in result.hard_failures}


def test_missing_specs_are_undetermined_not_guessed(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-07")

    result = match_one(demand, supplier)
    assert result.status == STATUS_UNDETERMINED
    assert result.hard_failures == []
    assert result.gaps, "必须列出缺口而不是编造匹配"
    assert "可服务海外区域" in " ".join(result.gaps)


def test_gulf_demand_matches_b200_supplier(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-02")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-02")

    result = match_one(demand, supplier)
    assert result.status == STATUS_MATCH
    assert result.score > 50


def test_match_demand_orders_by_status(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    results = match_demand(demand, suppliers)

    assert results[0].status == STATUS_MATCH
    statuses = [r.status for r in results]
    assert statuses.index(STATUS_MATCH) < statuses.index(STATUS_UNDETERMINED)
    assert statuses.index(STATUS_UNDETERMINED) < statuses.index(STATUS_NO_MATCH)


def test_registry_samples_yield_no_fabricated_match() -> None:
    """真实公开档案缺规格：结论必须是「无法判定」，不得凭空匹配。"""
    repo_root = Path(__file__).resolve().parents[1]
    suppliers = load_suppliers(repo_root / "data" / "samples" / "suppliers_sample.csv")
    demands = load_demands(repo_root / "data" / "samples" / "demands_sample.csv")

    assert suppliers and demands
    demand = next(d for d in demands if d.demand_id == "DEM-20261102-001")
    results = match_demand(demand, suppliers)

    assert all(r.status != STATUS_MATCH for r in results)
    assert all(r.gaps for r in results if r.status == STATUS_UNDETERMINED)


def test_report_contains_no_contact_or_price_fields(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    results = match_demand(demand, suppliers)

    text = render_summary(results, demand_label=demand.demand_id)
    record = render_match_record(results[0])
    clarify = render_clarify(results[0])

    for content in (text, record, clarify):
        assert "@" not in content.replace("[@", "(")  # 不得出现邮箱
        assert "USD" not in content
        assert "unit_price" not in content
    assert "不匹配原因" in text


def test_undetermined_result_next_step_forbids_outreach(synthetic) -> None:
    suppliers, demands = synthetic
    demand = next(d for d in demands if d.demand_id == "SYN-DEM-01")
    supplier = next(s for s in suppliers if s.supplier_id == "SYN-SUP-07")
    result = match_one(demand, supplier)
    assert "补规格缺口" in result.next_step
