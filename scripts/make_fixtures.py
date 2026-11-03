"""生成可复现的测试夹具与样例档案。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\make_fixtures.py

产物：
- ``tests/fixtures/``：合成夹具（HTML / PDF / DOCX / XLSX / 供需 CSV）
  **全部为 SYNTHETIC 数据，不是任何真实主体**，仅用于解析器与匹配算法回归。
- ``data/samples/``：来自 B 侧 ``registry.csv`` 的公开档案骨架，
  规格字段一律留空（未披露），禁止为了「看起来完整」而填造数值。

重跑会覆盖同名文件；已提交的产物不必重跑。
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
SAMPLES = REPO_ROOT / "data" / "samples"


# --------------------------------------------------------------------------- 供需样例


SUPPLIER_COLUMNS = [
    "supplier_id",
    "name",
    "home_region",
    "serves_regions",
    "gpu_models",
    "gpu_quantity",
    "max_delivery_weeks",
    "compliance_tags",
    "status",
    "evidence_ids",
    "notes",
]

DEMAND_COLUMNS = [
    "demand_id",
    "name",
    "country",
    "target_regions",
    "required_gpu_models",
    "required_gpu_quantity",
    "max_delivery_weeks",
    "required_compliance_tags",
    "budget_tier",
    "status",
    "evidence_ids",
    "notes",
]

#: 供应侧：源自 B-中国算力出海/01-供应侧档案/registry.csv（规格未披露即留空）
SAMPLE_SUPPLIERS = [
    {
        "supplier_id": "SUP-20261102-001",
        "name": "阿里云",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "evidence_ids": "B-E04|B-E06",
        "notes": "公开档案仅确认云厂商身份与东南亚合资叙述；卡型/容量/交付周/合规标签均未披露",
    },
    {
        "supplier_id": "SUP-20261102-002",
        "name": "腾讯",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "evidence_ids": "B-E04",
        "notes": "双向角色（亦为需求侧）；规格未披露",
    },
    {
        "supplier_id": "SUP-20261102-003",
        "name": "华为",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "evidence_ids": "B-E04",
        "notes": "含JV公开叙述；规格未披露",
    },
    {
        "supplier_id": "SUP-20261102-004",
        "name": "中国移动",
        "home_region": "中国",
        "evidence_ids": "B-E06",
        "notes": "信通院布局叙述；无可售容量表",
    },
    {
        "supplier_id": "SUP-20261102-005",
        "name": "中国电信",
        "home_region": "中国",
        "evidence_ids": "B-E06",
        "notes": "信通院布局叙述；无可售容量表",
    },
    {
        "supplier_id": "SUP-20261102-006",
        "name": "中国联通",
        "home_region": "中国",
        "evidence_ids": "B-E06",
        "notes": "信通院布局叙述；无可售容量表",
    },
    {
        "supplier_id": "SUP-20261102-007",
        "name": "Aolani",
        "home_region": "",
        "serves_regions": "东南亚",
        "evidence_ids": "B-E03",
        "notes": "CNBC具名；官网待补",
    },
]

#: 需求侧：源自 B-中国算力出海/02-海外需求侧档案/registry.csv
SAMPLE_DEMANDS = [
    {
        "demand_id": "DEM-20261102-001",
        "name": "ByteDance",
        "country": "中国",
        "target_regions": "东南亚",
        "evidence_ids": "B-E02|B-E03",
        "notes": "B-E02正文401失败；公司多未置评（不得当已确认买家）",
    },
    {
        "demand_id": "DEM-20261102-002",
        "name": "Alibaba",
        "country": "中国",
        "target_regions": "东南亚",
        "evidence_ids": "B-E02|B-E03",
        "notes": "与SUP-20261102-001双向；B-E02待核验",
    },
    {
        "demand_id": "DEM-20261102-003",
        "name": "Tencent",
        "country": "中国",
        "target_regions": "东南亚",
        "evidence_ids": "B-E03",
        "notes": "与SUP-20261102-002双向；公司多未置评",
    },
    {
        "demand_id": "DEM-20261102-004",
        "name": "DeepSeek",
        "country": "中国",
        "target_regions": "东南亚",
        "evidence_ids": "B-E02",
        "notes": "报道称例外，非确认海外租用；勿作已确认买家",
    },
    {
        "demand_id": "DEM-20261102-005",
        "name": "HUMAIN",
        "country": "沙特阿拉伯",
        "target_regions": "中东",
        "evidence_ids": "B-E12|B-E13",
        "notes": "公开计划至多数十万GPU；获准规模有限",
    },
    {
        "demand_id": "DEM-20261102-006",
        "name": "G42",
        "country": "阿联酋",
        "target_regions": "中东",
        "evidence_ids": "B-E13|B-E14|B-E15",
        "notes": "1GW Stargate；获准约3.5万Blackwell等值",
    },
    {
        "demand_id": "DEM-20261102-007",
        "name": "Sea Limited",
        "country": "新加坡/马来西亚",
        "target_regions": "东南亚",
        "evidence_ids": "B-E17|B-E18",
        "notes": "锚定租户，非确认GPU云租户",
    },
    {
        "demand_id": "DEM-20261102-008",
        "name": "Nebius",
        "country": "国际/沙特NEOM落点",
        "target_regions": "中东",
        "evidence_ids": "B-E16",
        "notes": "初步框架；拟2027Q4起分阶段",
    },
    {
        "demand_id": "DEM-20261102-009",
        "name": "GoTo Group",
        "country": "印度尼西亚",
        "target_regions": "东南亚",
        "evidence_ids": "B-E19",
        "notes": "未披露GPU规格",
    },
]

#: 合成夹具：覆盖每条硬过滤原因码（**非真实主体**）
FIXTURE_SUPPLIERS = [
    {
        "supplier_id": "SYN-SUP-01",
        "name": "AlphaCompute（合成）",
        "home_region": "中国",
        "serves_regions": "东南亚|中东",
        "gpu_models": "H100|H200",
        "gpu_quantity": "2000",
        "max_delivery_weeks": "8",
        "compliance_tags": "数据驻留|出口管制-允许",
        "status": "可报价",
        "evidence_ids": "B-E01|B-E02",
        "notes": "SYNTHETIC：应判定为可匹配",
    },
    {
        "supplier_id": "SYN-SUP-02",
        "name": "BetaDC（合成）",
        "home_region": "阿联酋",
        "serves_regions": "中东",
        "gpu_models": "B200",
        "gpu_quantity": "5000",
        "max_delivery_weeks": "4",
        "compliance_tags": "数据驻留",
        "status": "已合作",
        "evidence_ids": "B-E05",
        "notes": "SYNTHETIC：区域与卡型均不匹配",
    },
    {
        "supplier_id": "SYN-SUP-03",
        "name": "GammaCloud（合成）",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "gpu_models": "H100",
        "gpu_quantity": "100",
        "max_delivery_weeks": "6",
        "compliance_tags": "数据驻留",
        "status": "已接触",
        "evidence_ids": "B-E06",
        "notes": "SYNTHETIC：容量不足",
    },
    {
        "supplier_id": "SYN-SUP-04",
        "name": "DeltaRack（合成）",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "gpu_models": "H100",
        "gpu_quantity": "3000",
        "max_delivery_weeks": "30",
        "compliance_tags": "数据驻留",
        "status": "可报价",
        "evidence_ids": "B-E07",
        "notes": "SYNTHETIC：交付周期过长",
    },
    {
        "supplier_id": "SYN-SUP-05",
        "name": "EpsilonAI（合成）",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "gpu_models": "H100",
        "gpu_quantity": "3000",
        "max_delivery_weeks": "8",
        "compliance_tags": "等保二级",
        "status": "线索",
        "evidence_ids": "B-E08",
        "notes": "SYNTHETIC：已披露「等保二级」，缺需求必备的「数据驻留」→ 合规缺口",
    },
    {
        "supplier_id": "SYN-SUP-06",
        "name": "ZetaCloud（合成）",
        "home_region": "中国",
        "serves_regions": "东南亚",
        "gpu_models": "H100",
        "gpu_quantity": "3000",
        "max_delivery_weeks": "8",
        "compliance_tags": "数据驻留",
        "status": "搁置",
        "evidence_ids": "B-E09",
        "notes": "SYNTHETIC：供方状态不活跃",
    },
    {
        "supplier_id": "SYN-SUP-07",
        "name": "EtaCloud（合成）",
        "home_region": "中国",
        "serves_regions": "",
        "gpu_models": "",
        "gpu_quantity": "",
        "max_delivery_weeks": "",
        "compliance_tags": "",
        "status": "线索",
        "evidence_ids": "",
        "notes": "SYNTHETIC：规格全缺 → 无法判定，不许猜",
    },
]

FIXTURE_DEMANDS = [
    {
        "demand_id": "SYN-DEM-01",
        "name": "Synthetic Buyer（合成）",
        "country": "新加坡",
        "target_regions": "东南亚",
        "required_gpu_models": "H100",
        "required_gpu_quantity": "1000",
        "max_delivery_weeks": "12",
        "required_compliance_tags": "数据驻留",
        "budget_tier": "百万美元级",
        "status": "需求明确",
        "evidence_ids": "B-E10",
        "notes": "SYNTHETIC：卡数1000、12周内交付、需数据驻留",
    },
    {
        "demand_id": "SYN-DEM-02",
        "name": "Synthetic Gulf Buyer（合成）",
        "country": "沙特阿拉伯",
        "target_regions": "中东",
        "required_gpu_models": "B200",
        "required_gpu_quantity": "4000",
        "max_delivery_weeks": "10",
        "required_compliance_tags": "数据驻留",
        "budget_tier": "千万美元级",
        "status": "评估中",
        "evidence_ids": "B-E11",
        "notes": "SYNTHETIC：用于检验中东区域与B200卡型",
    },
]


# --------------------------------------------------------------------------- 解析夹具

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>合成夹具：AI 服务器供需观察</title>
  <style>body{font-family:sans-serif}</style>
</head>
<body>
  <nav><a href="/">首页</a> | <a href="/news">新闻</a></nav>
  <script>console.log("这段脚本不应进入正文");</script>
  <article>
    <h1>合成夹具：AI 服务器供需观察</h1>
    <p>本文为 compute-dual 解析器的测试夹具，全部内容为合成示例，不对应任何真实主体或真实数据。</p>
    <p>第二段用于验证正文抽取：加速卡供给与交付周期是供需匹配的硬约束，交付周期通常以周为单位披露。</p>
    <p>第三段用于验证噪声剔除：页面导航与脚本内容不应出现在抽取结果中。</p>
  </article>
  <footer>© 合成夹具</footer>
</body>
</html>
"""

SAMPLE_PDF_PAGES = [
    (
        "合成夹具 PDF 第 1 页",
        [
            "compute-dual 解析器测试夹具（合成内容，非真实数据）",
            "本页用于验证 PDF 文本抽取与页码标记。",
            "关键约束：交付周期、卡型、可服务区域、合规标签。",
        ],
    ),
    (
        "合成夹具 PDF 第 2 页",
        [
            "第二页用于验证多页合并与 page_count 统计。",
            "若抽取失败，测试必须失败，而不是回退成空正文。",
        ],
    ),
]


def write_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def build_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    for title, lines in SAMPLE_PDF_PAGES:
        page = document.new_page()
        page.insert_text((72, 90), title, fontsize=16, fontname="china-s")
        y = 130
        for line in lines:
            page.insert_text((72, y), line, fontsize=11, fontname="china-s")
            y += 24
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    document.close()


def build_docx(path: Path) -> None:
    import docx

    document = docx.Document()
    document.add_heading("合成夹具 DOCX", level=1)
    document.add_paragraph("本文件由 scripts/make_fixtures.py 生成，用于验证 DOCX 解析（合成内容）。")
    table = document.add_table(rows=3, cols=3)
    rows = [
        ("卡型", "数量", "交付周"),
        ("H100", "1000", "12"),
        ("B200", "500", "8"),
    ]
    for row_index, row in enumerate(rows):
        for col_index, value in enumerate(row):
            table.cell(row_index, col_index).text = value
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))


def build_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "合成夹具"
    for row in [
        ("区域", "卡型", "可供卡数", "交付周"),
        ("东南亚", "H100", 2000, 8),
        ("中东", "B200", 5000, 4),
    ]:
        sheet.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(str(path))


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)

    write_csv(SAMPLES / "suppliers_sample.csv", SUPPLIER_COLUMNS, SAMPLE_SUPPLIERS)
    write_csv(SAMPLES / "demands_sample.csv", DEMAND_COLUMNS, SAMPLE_DEMANDS)
    write_csv(FIXTURES / "suppliers_fixture.csv", SUPPLIER_COLUMNS, FIXTURE_SUPPLIERS)
    write_csv(FIXTURES / "demands_fixture.csv", DEMAND_COLUMNS, FIXTURE_DEMANDS)

    (FIXTURES / "sample.html").write_text(SAMPLE_HTML, encoding="utf-8")
    build_pdf(FIXTURES / "sample.pdf")
    build_docx(FIXTURES / "sample.docx")
    build_xlsx(FIXTURES / "sample.xlsx")

    print(f"夹具已生成：{FIXTURES}")
    for path in sorted(FIXTURES.iterdir()):
        print(f"  - {path.name} ({path.stat().st_size} bytes)")
    print(f"样例已生成：{SAMPLES}")
    for path in sorted(SAMPLES.iterdir()):
        print(f"  - {path.name} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
