# 算力双项目 MVP 验收演示（Windows / PowerShell）
#
# 用途：一条命令跑完《DeepSeek-Harness交接说明》§4 的五条验收口令。
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\demo.ps1
#
# 前置：已按 README 建好 .venv 并 pip install -e ".[dev]"
# 说明：脚本从仓库根运行；预期会失败的步骤（如未授权导出）只打印退出码，不中断演示。

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$ak = Join-Path $repoRoot '.venv\Scripts\ak.exe'
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $ak)) {
    Write-Host "找不到 $ak —— 请先按 README 建 venv 并安装" -ForegroundColor Red
    exit 1
}

function Section($title) {
    Write-Host ''
    Write-Host ('=' * 78) -ForegroundColor DarkGray
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host ('=' * 78) -ForegroundColor DarkGray
}

function Step($title) {
    Write-Host ''
    Write-Host "▶ $title" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 0. 环境
Section '0. 环境自检（ak doctor）'
& $python -m pip check
& $ak doctor

# ---------------------------------------------------------------- 1. 建库
Section '1. 研究库初始化'
& $ak db init

# ---------------------------------------------------------------- 2. 证据导入与取回
Section '2. 证据管理（T12）：导入交接包真实证据表 → 按 ID 取回'
Step '导入 A 项目证据表（docs/handoff/A-算力之王AK/证据表.md）'
& $ak evidence import-md 'docs/handoff/A-算力之王AK/证据表.md' --strict
Step '导入 B 项目证据表（docs/handoff/B-中国算力出海/证据表.md）'
# 不指定 --strict：B 表有 1 行源数据列错位（B-E07 的「事实/推断/假设」列填成了「待核验」），
# 导入器按设计跳过并报告原因，不擅自推断该条标签。
& $ak evidence import-md 'docs/handoff/B-中国算力出海/证据表.md'
Write-Host '（B-E07 被跳过属预期：源表第 3 列填的是核验状态，需人工改表后重导）' -ForegroundColor DarkYellow
Step '按 ID 取回 A-E15（NVIDIA FY25 财报级数字）'
& $ak evidence get A-E15
Step '硬闸门验证：fact 无 URL 必须被拒绝（预期退出码 2）'
& $ak evidence add --project ak --tag fact --statement '无来源的结论' --claim-id A-E90
Write-Host "退出码：$LASTEXITCODE（预期 2 = 闸门拒绝）" -ForegroundColor DarkYellow
Step '状态机验证：A-E03 推进为 disputed（口径冲突待消）'
& $ak evidence verify A-E03 --status disputed --note 'NVIDIA 市占 70% vs >80% 口径未统一'
Step '非法迁移验证：rejected 之后不得回滚（预期失败）'
& $ak evidence verify A-E04 --status rejected --note '演示用：标记否决'
& $ak evidence verify A-E04 --status cross_checked
Write-Host "退出码：$LASTEXITCODE（预期 2 = 非法迁移）" -ForegroundColor DarkYellow

# ---------------------------------------------------------------- 3. 抓取解析
Section '3. 抓取/解析（T13）：对 tests/fixtures 跑通解析'
& $ak fetch from-file 'tests/fixtures/sample.pdf' --project ak
Write-Host "退出码：$LASTEXITCODE" -ForegroundColor DarkYellow
& $ak fetch from-file 'tests/fixtures/sample.docx' --project ak
& $ak fetch from-file 'tests/fixtures/sample.xlsx' --project ak
& $ak fetch from-file 'tests/fixtures/sample.html' --project ak
Step '搜索层诚实性：未配置 Provider 时显式失败，不返回伪造命中'
& $ak fetch search 'AI 服务器 产业链'
Write-Host "退出码：$LASTEXITCODE（预期 1 = 未配置）" -ForegroundColor DarkYellow

# ---------------------------------------------------------------- 4. 研究工单
Section '4. 研究工单（T14）：草稿带 [@claim_id] 引用'
Step '新建工单并拆解问题'
$ticketOut = & $ak ticket new --project ak --topic 'AI服务器产业链：液冷与光模块' --scope '只用公开来源；推断必须标注'
$ticketOut | Write-Host
$ticketId = (& $ak ticket list --project ak | Select-String -Pattern 'T-\d{8}-\d{3}' | Select-Object -First 1).Matches.Value
Write-Host "工单：$ticketId" -ForegroundColor DarkYellow

& $ak ticket ask $ticketId 'AI 数据中心液冷渗透率的最新机构估计'
& $ak ticket ask $ticketId '光模块 800G/1.6T 的公司指引与行业口径'
& $ak ticket ask $ticketId '本轮未研究的空白项（数据缺口）'

Step '把证据挂到问题上'
& $ak research link $ticketId "$ticketId-Q1" A-E22
& $ak research link $ticketId "$ticketId-Q1" A-E23
& $ak research link $ticketId "$ticketId-Q2" A-E19
& $ak research link $ticketId "$ticketId-Q2" A-E20
& $ak research link $ticketId "$ticketId-Q2" A-E21
& $ak research link $ticketId "$ticketId-Q3" A-E21
& $ak research link $ticketId "$ticketId-Q3" A-E29

Step '生成草稿（含缺口标注，结论区留白）'
& $ak research draft $ticketId --out 'data/exports/draft-A.md'

Step 'QA 自检（无摘录率 / 冲突 / 缺失）'
& $ak qa check --project ak

# ---------------------------------------------------------------- 5. 匹配
Section '5. 供需匹配（T15）：硬过滤 + 不匹配原因'
Step '真实公开档案（规格未披露 → 必须「无法判定」，不得编造匹配）'
& $ak match samples
& $ak match run --demand DEM-20261102-001
Write-Host "退出码：$LASTEXITCODE" -ForegroundColor DarkYellow

Step '合成夹具（覆盖每条硬过滤原因码；SYNTHETIC 非真实主体）'
& $ak match run --demand SYN-DEM-01 --suppliers tests/fixtures/suppliers_fixture.csv --demands tests/fixtures/demands_fixture.csv

Step '生成脱敏匹配报告'
& $ak match report --demand SYN-DEM-01 --suppliers tests/fixtures/suppliers_fixture.csv --demands tests/fixtures/demands_fixture.csv --out-dir data/exports
& $ak match clarify --demand SYN-DEM-01 --supplier SYN-SUP-07 --suppliers tests/fixtures/suppliers_fixture.csv --demands tests/fixtures/demands_fixture.csv --out 'data/exports/clarify-SYN-SUP-07.md'

# ---------------------------------------------------------------- 6. 敏感隔离
Section '6. 敏感隔离（T16）：L2 私密库 + 审计'
& $ak private init --purpose '初始化商务敏感库'
& $ak private add-contact --purpose '录入合成联系人用于演示' --subject 'SUP-20261102-001' --name '合成联系人（演示）' --title '商务负责人' --email 'demo@example.invalid' --org 'Synthetic Org'
Step '未授权导出必须被拒绝（预期退出码 3）'
& $ak private export --purpose '尝试未授权导出' --table contacts --out 'data/exports/contacts.csv'
Write-Host "退出码：$LASTEXITCODE（预期 3 = 拒绝）" -ForegroundColor DarkYellow
Step '显式授权导出（写入审计）'
& $ak private export --purpose '向 Arthur 汇报演示' --table contacts --out 'data/exports/contacts.csv' --allow-export
Step '查看审计日志'
& $ak private audit --purpose '检查审计记录' --limit 5
Step '隔离证明：研究库无 L2 表/列，跨库查询被拒'
& $ak private isolation

# ---------------------------------------------------------------- 7. git 忽略
Section '7. 敏感文件不入库（T16 验收）'
Write-Host 'git check-ignore 结果（有输出即表示已被忽略）：'
git check-ignore -v data/private/biz_private.db data/research.db 2>&1
Write-Host ''
Write-Host 'git status 中的敏感文件（应为空）：'
git status --porcelain 2>&1 | Select-String -Pattern 'private|research\.db|exports'
Write-Host "（无输出 = 未被跟踪）" -ForegroundColor DarkYellow

# ---------------------------------------------------------------- 8. 测试
Section '8. 回归测试'
& $python -m pytest -q
Write-Host "pytest 退出码：$LASTEXITCODE（0 = 全绿）" -ForegroundColor DarkYellow

Section '演示结束'
Write-Host '产物位置：'
Write-Host "  data/research.db                          研究库（gitignored）"
Write-Host "  data/private/biz_private.db               L2 私密库（gitignored）"
Write-Host "  data/exports/draft-A.md                   研究草稿（带 [@claim_id]）"
Write-Host "  data/exports/match-summary-*.md           匹配汇总（含不匹配原因）"
Write-Host "  data/logs/tool_calls.jsonl                工具调用结构化日志"
