# 算力双项目 MVP（compute-dual）

本地优先的共享工具层 MVP：**证据管理 → 抓取解析 → 研究 ticket 工作流 → 供需硬过滤匹配 → 敏感数据隔离**。

> 状态：**已完成 T10–T16 的最小可运行实现**（有 pytest 覆盖）。
> **未做**：云部署、付费会员、撮合交易自动化、自动外发商务信息、LLM 自动结论。
> 权威设计文档在 `docs/handoff/`，实现说明在 `docs/dev/`。

---

## 0. 快速开始（Windows / PowerShell）

```powershell
# 1) 环境（要求 Python 3.11+，本机实测 3.12.12）
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip check          # 应无冲突

# 2) 初始化数据库（研究库 data/research.db）
.\.venv\Scripts\ak.exe db init

# 3) 环境自检
.\.venv\Scripts\ak.exe doctor

# 4) 测试
.\.venv\Scripts\python.exe -m pytest
```

用 `uv` 更快（本机实测）：

```powershell
uv venv .venv --python 3.12 --seed      # --seed 会装上 pip，便于 pip check
uv pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pip check
```

> 实测环境：Windows + Python 3.12.12 + uv 0.12.12；`pip check` = No broken requirements found。
> 完整实现说明（含与设计文档的偏差、已知限制）见 `docs/dev/MVP实现说明.md`。

---

## 1. 验收口令（对应交接文档 §4）

| # | 验收动作 | 命令 |
|---|----------|------|
| 1 | 干净 venv 按 README 安装 | 上面 0. 的四行 |
| 2 | 一条 CLI 插入证据并查回 | `ak evidence add ...` → `ak evidence get A-E30 --json` |
| 3 | 对 `tests/fixtures` 跑通解析 | `ak fetch from-file tests/fixtures/sample.pdf --project ak` |
| 4 | 匹配样例带「不匹配原因」 | `ak match run --demand DEM-20261102-001` |
| 5 | `biz_private` 不在 git 跟踪中 | `git check-ignore -v data/private/biz_private.db` |

一条命令跑完 2–5：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\demo.ps1
```

---

## 2. CLI 速查

```text
ak doctor                                    # 环境/deps/时区/DB 自检
ak db init [--force]                         # 建研究库表
ak evidence add --project ak --tag fact --statement "..." --url https://... --source "..." --excerpt "..."
ak evidence get A-E30 [--json]                # 按 ID 取回
ak evidence list --project ak --tag fact --status unverified
ak evidence verify A-E03 --status disputed --note "口径冲突"
ak evidence import-md docs/handoff/A-算力之王AK/证据表.md   # 从既有 markdown 证据表导入
ak evidence export-md --project ak --out out/证据表.md      # 从库再生成 markdown
ak fetch ingest --url https://... --project ak [--ticket T-... --question T-...-Q1]
ak fetch from-file tests/fixtures/sample.pdf --project ak
ak ticket new --project ak --topic "AI服务器产业链" --scope "只做公开信息"
ak ticket ask T-20261102-001 "HBM 需求增速的可靠来源"
ak research link T-20261102-001 T-20261102-001-Q1 A-E09
ak research draft T-20261102-001 --out out/draft.md          # 产出含 [@claim_id] 的 md
ak qa check --project ak                                     # 断摘录率/标签分布/冲突
ak match load --suppliers data/samples/suppliers_sample.csv --demands data/samples/demands_sample.csv
ak match run --demand DEM-20261102-001 [--json]              # 含硬过滤失败原因
ak match report --out out/match_report.md
ak private init                                              # 建私密库 data/private/biz_private.db
ak private add-contact --subject SUP-20261102-001 --name "..." --email "..." --purpose "商务跟进"
ak private export --table contacts --purpose "向Arthur汇报" --out out/contacts.csv  # 强制审计
ak private audit --limit 20
```

> **`--json` 输出约定**：所有 `--json` 都是 UTF-8 纯 JSON（无颜色、不换行折断），可直接
> `| jq` 或 `> out.json` + `python -m json.tool`。
> 注意 Windows PowerShell 5.1 的 `>` 会把子进程输出重新编码成 UTF-16，
> 需要原样字节时用 `cmd /c "ak ... --json > out.json"` 或
> `... | Set-Content -Encoding utf8 out.json`。

---

## 3. 硬约束（实现层已落地，不是文案）

| 约束 | 落地方式 |
|------|----------|
| `fact` 必须有可打开 URL | `EvidenceClaim` 校验器 + DB 层拒绝（`EvidenceGateError`） |
| 失败不伪造结果 | fetch 非 2xx 只记状态码与 `error_kind`，不生成正文、不产生 claim |
| 冲突禁止静默覆盖 | `evidence verify` 状态机 `unverified→cross_checked→disputed→rejected`，非法迁移报错 |
| 推断/假设不得当事实写进产品 | `epistemic_tag` 入库并随报告渲染，`missing` 单列「数据缺口」 |
| 商务敏感数据与研究库隔离 | 两个独立 SQLite 文件；研究库 schema 无 contacts/quotes/deal_logs 表，有测试断言 |
| L2 导出必留审计 | `private export` 无 `--purpose` 直接拒绝，成功即写 `audit_log` |
| 时间统一 Asia/Shanghai | `retrieved_at_shanghai` 强校验 `+08:00`，Windows 依赖 `tzdata` |
| 敏感原文不送第三方 | MVP 无 LLM 调用；报告只引用库内摘录 |

---

## 4. 目录

```text
src/compute_dual/
  config.py            路径与设置（COMPUTE_DATA_DIR 可覆盖）
  cli.py               Typer 入口（ak）
  common/              时区、ID、结构化日志、错误分类
  evidence/            证据模型 + 研究库（SQLite）+ 证据表 markdown 互转
  tools/               fetch / 解析（PDF·DOCX·XLSX·HTML）/ 正文抽取
  research/            ticket、问题、草稿生成、QA
  matching/            供需 CSV、硬过滤、匹配报告
  private/             biz_private.db、L2 授权闸门、审计日志
tests/                 pytest（fixtures 为合成数据，非真实主体）
data/samples/          供需 CSV 样例（源自 B 侧 registry.csv，规格未知即留空）
docs/handoff/          原始交接包（权威设计）
docs/dev/              本次实现说明与实测记录
scripts/               demo.ps1 / make_fixtures.py
```

---

## 5. 边界（不得对外表述为已就绪）

- 无云部署、无 FastAPI 服务、无多用户权限 UI（`T17`–`T24` 仍为仅规划）。
- 无 LLM 自动生成结论；`research draft` 只做「证据编排 + 缺口标注」，结论需人工写。
- `data/samples/` 的真实主体规格字段多为空缺，匹配会返回「规格缺口」而非编造匹配。
- 搜索层只提供**人工入队**与**可插拔 Provider 空壳**，未接入任何付费搜索 API。
