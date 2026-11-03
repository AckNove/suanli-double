# 补搜补丁说明 — SiC 试研（极短）

> 日期：2026-11-02（Asia/Shanghai）  
> 性质：T17 全量试研过程中的多源打开记录，非第二轮长篇加戏

## 已打开并入库

- 国际：ST Catania 新闻稿；Infineon Kulim（2023）与 200mm 产品发放（2025）；ROHM–SiCrystal–ST 供货稿；TrendForce 衬底收入/份额新闻稿（+ Semiconductor Today 交叉）  
- 国内年报 PDF：天岳 2024/2025、三安 2024、斯达 2024、时代电气 2024、晶升 2024、精测 2024、锦浪 2024  

## 尝试失败（已【缺失】占位）

- Wolfspeed SEC/PublicNow/官网新闻正文（403 或空壳）  
- Yole 两则新闻稿（CloudFront 403）  
- onsemi IR PDF / BusinessWire（403/失败）  
- 七部门未来产业意见正文无「碳化硅」字样 → 不作 SiC 专项政策证据  

## 产物

研究稿 + 证据表 + 衬底市占冲突页 + A 股三栏 + 待补搜清单；见同目录文件名。


---

## P0 衬底市占回源结果表（2026-11-03 Asia/Shanghai）

| 项 | 结果 | 证据 ID | 备注 |
|----|------|---------|------|
| 1. TrendForce 2024 N 型份额方法注 | **部分成功** | S-E07 改写；**S-E24** 新建 | CN/EN 再开；确认**营收市占**；captive/分母细则仍未公开 |
| 2. 富士经济原报告或独立第二源 | **部分成功** | S-E25（目录）；**S-E26**（券商 22.80%）；S-E27/S-E28（官网/媒体） | 原件 PDF【缺失】；维持「转述·原件缺失」；冲突未消且 2024 % 与 TrendForce 加深差 |
| 3. Wolfspeed 一手 10-K/业绩稿 | **成功** | **S-E19 升级** | 10-K HTML + Earnings PDF 打开；Materials $343.6M/$391.6M；禁混算市占 |
| 4. Yole 公开稿（可选） | **失败** | S-E20 仍缺失 | curl 403 CloudFront |
| 冲突是否仍未消 | **是** | 冲突页已更新 | **对外仍禁单点市占%** |
| 未动 | — | — | 未开 AI Server P0 / HBM；未改仓内科普定稿正文 |

### 本轮尝试失败 URL（记错误）

- `https://www.yolegroup.com/press-release/power-sic-faces-overcapacity-downturn-while-china-accelerates-domestic-equipment-ambitions/` → HTTP 403  
- `https://www.yolegroup.com/press-release/yg-press-news-power-sic-a-10b-market-by-2029-facing-short-term-turbulence/` → HTTP 403  
- `https://www.sec.gov/Archives/edgar/data/895419/000089541925000110/0000895419-25-000110.txt` → HTTP 403（HTML 版可用）  
- `https://ebs.publicnow.com/view/85F0759A664D23975410B5C0FE5BB08CAF53FB81` → 本轮 curl 403（有公开转载摘录，但未当一手入库）

## 产业研究员核验（2026-11-03）

同意冲突仍未消、公域禁单点市占%。抽检 S-E24/26/27 与 Wolfspeed 业绩数字通过；**S-E19 定义句已降「部分核验」**（Earnings 已核、SEC 10-K HTML 403）。富士原件仍缺；仓内 v0.1.1 不动。纪要：`核验-SiC-P0衬底市占-20261103.md`。
