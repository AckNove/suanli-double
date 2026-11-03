# MVP 实现说明（T10–T16）

> 状态：**已完成并有测试**（`pytest` 136 passed）
> 实现日期：2026-11-02（Asia/Shanghai）
> 实现者：DeepSeek Harness
> 依据：`docs/handoff/`（权威设计）— 《基础设施设计》《目录规范与接口约定 v0.2》
> 《后续开发计划》《DeepSeek-Harness交接说明》

本文件说明「设计→代码」的落点、与设计文档的**偏差**、以及**未做**的部分。
凡未在此列出的能力，一律视为未实现。

---

## 0. 一句话结论

T10–T16 七项已从「需本地环境」变为「已完成」：**证据库、抓取解析、研究工单草稿、
供需硬过滤、敏感隔离** 均可复现运行，且**没有**把推断写成事实、**没有**编造任何
客户/报价/搜索结果。

---

## 1. 验收命令（最短复现）

```powershell
# 环境（实测 Python 3.12.12 + uv，pip check 通过）
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 全部验收一次性跑完（含预期失败项）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\demo.ps1

# 回归测试
.\.venv\Scripts\python.exe -m pytest
```

逐条对应《DeepSeek-Harness交接说明 §4》：

| 验收口令 | 命令 | 实测结果 |
|----------|------|----------|
| ① 干净 venv 按 README 安装 | 上面三行 | `pip check` = No broken requirements found |
| ② 一条 CLI 插入证据并查回 | `ak evidence add ...` → `ak evidence get A-E30 --json` | 通过；`retrieved_at_shanghai` 带 `+08:00` |
| ③ 对 fixtures 跑通解析 | `ak fetch from-file tests/fixtures/sample.pdf` | PDF 2 页 / 163 字；DOCX 表 1；XLSX 表 1；HTML 去导航成功 |
| ④ 匹配样例含「不匹配原因」 | `ak match run --demand SYN-DEM-01 --suppliers tests/fixtures/suppliers_fixture.csv --demands tests/fixtures/demands_fixture.csv` | 6 类硬失败原因码全部可复现 |
| ⑤ `biz_private` 不在 git 跟踪 | `git check-ignore -v data/private/biz_private.db` | 命中 `.gitignore:34:data/private/` |

真实运行日志见 `实测记录-demo输出.log`（434 行，未删改）。

---

## 2. 任务映射（T10–T16）

| ID | 交付物 | 代码位置 | 验收证据 |
|----|--------|----------|----------|
| T10 | 私仓结构 | `pyproject.toml`、`src/compute_dual/`、`tests/`、`scripts/`、`data/`、`.gitignore`、`.env.example` | `ak doctor` 通过；目录树见 README §4 |
| T11 | venv 与依赖 | `pyproject.toml`（9 个运行时依赖 + pytest） | `pip check` 无冲突；`ak doctor` 逐项显示依赖已装 |
| T12 | Evidence DB | `evidence/models.py`、`evidence/db.py`、`evidence/markdown_io.py`、CLI `evidence *` | 导入 A 表 29 条 / 0 跳过；`test_evidence_db.py` 断言插入→取回、状态机、DB CHECK |
| T13 | Fetch/Parse | `tools/fetch.py`、`tools/parse_file.py`、`tools/textract.py`、`research/ingest.py` | fixtures 四类文件全通；403/401/429/451 只记状态码不采信正文 |
| T14 | 研究工单 | `research/tickets.py`、`research/draft.py`、`research/qa.py`、CLI `ticket * / research * / qa *` | `data/exports/draft-A.md` 含 `[@A-E22]` 等 4 条引用与 4 项缺口，明确标注「不得对外」 |
| T15 | 供需匹配 | `matching/csvio.py`、`matching/hardfilter.py`、`matching/report.py`、CLI `match *` | 6 个原因码各有一条用例；脱敏报告 8 份文件 |
| T16 | 私密库与审计 | `private/store.py`、`private/guard.py`、CLI `private *` | 未授权导出退出码 3；授权导出写审计；`ak private isolation` 返回 `isolated: true` |

---

## 3. 硬约束的落地位置（不是文案承诺）

| 约束 | 落地位置 | 验证方式 |
|------|----------|----------|
| `fact` 必须有可打开 URL | `EvidenceClaim._gate`（pydantic）+ `evidence_claims` 表 CHECK | `test_fact_without_url_is_rejected`、`test_db_check_constraint_blocks_fact_without_url`（直接 SQL 插入也被拦） |
| 抓不到不编造 | `tools/fetch.fetch` 非 2xx 时 `body=b""`；`ingest_url` 记 `parse_status='fetch_failed'` | `test_fetch_policy_block_records_status_without_body` |
| 搜索未配置必须显式失败 | `UnconfiguredSearchProvider.search` 抛 `FetchError(error_kind='budget')` | `test_search_provider_is_explicitly_unconfigured`、`ak fetch search` 退出码 1 |
| 冲突禁止静默覆盖 | `ALLOWED_TRANSITIONS` + `evidence_status_log` 留痕 | `test_illegal_transition_rejected` |
| 推断/假设不得当事实 | `epistemic_tag` 入库并随报告渲染；`missing` 单列「数据缺口」 | `test_draft_cites_claims_and_flags_gaps` |
| 敏感数据隔离 | 两个 SQLite 文件；研究库 schema 无 contacts/quotes/deal_logs | `verify_isolation` + `test_cross_query_is_blocked` |
| 导出 L2 必留审计 | `PrivateStore.export_rows` 先查 `allow_export`，再写 `audit_log` | `test_export_requires_explicit_permission`、`test_export_writes_audit_row` |
| 时间统一 Asia/Shanghai | `common/timeutil.py`，模型强校验 `+08:00`；Windows 依赖 `tzdata` | `test_now_shanghai_has_plus8_offset`、`test_retrieved_at_converted_to_shanghai` |
| 敏感原文不送第三方 | 全链路**无任何 LLM 调用** | 依赖清单无 LLM SDK；`grep -r "openai\|anthropic" src/` 为空 |

---

## 4. 与设计文档的偏差（需 Arthur 确认）

### 4.1 证据表新增 3 个可选字段

《目录规范 v0.2 §2》规定 14 个逻辑字段。为了让交接包里**既有的 markdown 证据表可无损往返**，
增加了 3 个可空字段（不改变原字段语义，也不影响任何必填校验）：

| 字段 | 用途 | 例子 |
|------|------|------|
| `epistemic_note` | 保留标签后的口径括注 | 原「事实（机构估计·转引）」→ `fact` + `机构估计·转引` |
| `verification_note` | 保留核验状态原文 | 原「未核验（单源但权威）」→ `unverified` + 原文 |
| `extra_urls` | 一条结论的其余来源链接 | A-E14 有三个 URL，首个进 `url`，其余进 `extra_urls` |

理由：不加这三个字段，导入时必然丢失「机构估计」「部分核验」这类关键限定，
等于把限定语抹平 —— 与「禁止把推断当事实」的方向相反。如不接受，可改为导入时只保留
首个来源并丢弃括注（会损失信息）。

### 4.2 搜索层

设计中「Provider 可插拔（SerpAPI / Bing / 本地浏览器）」本期**只实现空壳 + 人工入队**
（`data/search_queue.txt`）。未接任何付费 API，也未用 Harness 的 WebSearch 冒充产品内搜索。
`ak fetch search` 的预期行为是**失败**。

### 4.3 `data/samples/` 的规格字段为空

`data/samples/suppliers_sample.csv` 与 `demands_sample.csv` 由 B 侧 `registry.csv` 转出，
**卡型 / 容量 / 交付周 / 合规标签一律留空**（公开档案里确实没有）。
因此对真实档案跑匹配会得到「无法判定（关键规格未披露）」，而不是编造出来的匹配。
这是刻意设计：用来暴露真实的数据缺口。

算法正确性用 `tests/fixtures/*.csv` 的**合成数据**验证（文件内标注 `SYNTHETIC`，非真实主体）。

### 4.4 「无法判定」不给匹配分数

`MatchResult.score` 仅在硬条件全部通过时计算；否则为 0，主观分显示 `—`。
理由：规格未披露却给 65 分 / 3.2 星，会诱导商务直接外联。

---

## 5. 已知限制 / 未做（不得对外表述为已就绪）

1. **无 LLM 写作**：`ak research draft` 只做「证据编排 + 缺口标注」，结论区故意留白。
2. **无 Web 服务**：只有 CLI；FastAPI、多用户权限 UI、云部署均未做（T19/T20 仍为仅规划）。
3. **无搜索引擎**：见 4.2。
4. **PDF 表格识别不保证**：`page.find_tables()` 对扫描件/复杂排版会漏；失败降级不报错。
   扫描件需 OCR，本期未接。
5. **SQLite 单机**：并发写不适合多人；迁 Postgres 需重写 `evidence/db.py` 的 DDL 与连接层（字段设计可直接映射）。
6. **L2 生产使用前的注意事项**：`biz_private.db` 是普通 SQLite 文件，
   建议放在 BitLocker/VeraCrypt 卷上，并配合 `COMPUTE_L2_LOCKED=1` 在非授权场景禁用访问。
   代码层隔离**不是**加密隔离。
7. **B-E07 无法导入**：B 侧证据表第 3 列（事实/推断/假设）填的是「待核验」，
   属源数据列错位。导入器按设计跳过并给出可定位原因，未擅自推断该条标签。
   修表后 `ak evidence import-md` 重跑即可（幂等）。

---

## 6. 交接包里发现的两处源数据问题（建议修表）

1. **`B-中国算力出海/证据表.md` B-E07**：第 3 列填「待核验」，应为 `fact`/`inference`/`hypothesis`/`missing` 之一；
   其核验列为「正文未成功抓取」，若按口径应属 `missing` 或 `hypothesis`。
2. **`A-算力之王AK/证据表.md` 第 27 行**：表格中夹了一个空行，把 A-E18 之后的 A-E19…A-E29 切成
   第二段表格。导入器能处理（按行扫描），但人读时容易被当成两节。

---

## 7. 复现产物（gitignored，本机生成）

| 路径 | 内容 |
|------|------|
| `data/research.db` | 研究库（A 29 条 + B 18 条证据、工单、QA 记录） |
| `data/private/biz_private.db` | L2 私密库（演示联系人 + 审计日志） |
| `data/raw/{project}/{yyyy}/{mm}/{doc_id}/` | 原件 + `meta.json` |
| `data/text/{doc_id}.md` | 抽取后的正文（带 doc_id 与页码标记） |
| `data/exports/draft-A.md` | 研究草稿（含 `[@claim_id]` 与缺口） |
| `data/exports/match-summary-*.md`、`MATCH-*.md` | 脱敏匹配报告 |
| `data/logs/tool_calls.jsonl` | 工具调用结构化日志（request_id / latency / error_kind） |
