# 电力政策知识库 Agent 交接说明

本文件给后续接入本仓库的 agent 使用。新对话开始后，请先读本文件，再读最新实验日志和 `git status`，不要从零猜项目状态。

## 项目目标

构建一个电力市场政策知识库，重点覆盖国家级、省级、直辖市、区域监管局、交易中心、电网企业等来源的政策文件和交易规则。主题包括电力市场、交易规则、中长期、现货、辅助服务、省内、省间、绿电、结算、注册、信息披露等。

当前知识库不是单纯爬虫项目，而是一条“来源清单 -> 候选发现 -> 入库 -> 清洗 -> 快照/PDF归档 -> 切片 -> RAG/搜索页”的流水线。

## 当前环境

- 工作目录：`D:\python代码\电力政策知识库`
- PowerShell 工作方式：Windows PowerShell
- Python 环境：`D:\miniconda\envs\py311\python.exe`
- Git remote：`https://github.com/JunFengXiang/electricity-policy-rag.git`
- 依赖文件：`requirements.txt`
- 当前重要提交：
  - `1c59597 Add agent handoff guide`：扩库前基线，远端 tag `baseline-20260501`
  - `e262d39 Expand policy knowledge base v1`：扩库、15天更新和本地问答 V1，远端 tag `expansion-v1-20260502`
  - `0dc262e Add PDF attachment entries to search`
  - `373047c Show snapshot type in search results`
  - `886f140 Add simulated page fallback for snapshots`
  - `94579a9 Prefer official page long screenshots`

## 当前数据状态

截至 2026-05-02：

- 基线 tag：`baseline-20260501`，指向扩库前提交 `1c59597`
- 扩库 V1 tag：`expansion-v1-20260502`，指向 `e262d39`
- 台账资料：419 条
- 知识切片：48270 条
- RAG 索引特征数：80000
- 网页快照：254 个
  - 官网长截图：253 个
  - 模拟截图：1 个
- PDF 附件入口：411 个
- PDF 附件下载失败：4 个，见 `05_输出成果/pdf_attachments.json`
- 数据验收报告：`05_输出成果/数据验收报告_20260502_021031.md`
- 候选人工复核队列：`05_输出成果/候选人工复核队列_20260502.csv`，614 条 50-54 分候选，不进入主索引
- 搜索页：`05_输出成果/search.html`
- 搜索索引：`05_输出成果/search_index.json`
- PDF 附件清单：`02_元数据/PDF附件清单.csv`
- PDF 附件索引：`05_输出成果/pdf_attachments.json`
- 网页快照清单：`05_输出成果/网页快照/snapshot_manifest.json`
- 15天更新报告：`06_实验日志/更新报告_20260502.md`

## 目录说明

- `00_说明`：项目说明文档。
- `01_原始资料`：下载的网页、PDF、DOCX 等原始文件。
- `01_原始资料/PDF附件`：从网页正文中发现并下载的 PDF 附件。
- `01_原始资料/解读资料`：官方解读、公众号/媒体解读等辅助解释资料；第三方资料先进入人工复核，不直接进入主问答索引。
- `02_元数据`：核心 CSV 台账、来源清单、规则清单、关联关系、附件清单。
- `03_处理后文本`：从 HTML/PDF/DOCX 提取出的文本。
- `04_标签与权重`：标签、权重相关资料。
- `05_输出成果`：搜索页、RAG 索引、网页快照、导出结果。
- `06_实验日志`：实验日志和操作日志。关键动作必须记录。
- `scripts`：全部流水线脚本。

## 核心约定

- 每次关键操作都要写入 `06_实验日志`，优先用 `scripts/log_action.py`。
- 不要随意删除原始资料、快照、PDF 附件、台账备份。
- 不要回退用户或其他 agent 已经做过的改动，除非用户明确要求。
- 修改脚本后必须跑 `compileall`。
- 修改搜索页相关数据后必须重新运行 `scripts/09_build_search_page.py`。
- 数据入库或批量改台账前，脚本应自动备份；如果没有备份逻辑，先补备份逻辑。
- 网页快照要求是截图形式，尽量使用官网长截图并保留网站标头；失败时才允许模拟截图。
- 如果网页中有 PDF 附件，必须下载 PDF，并在搜索页中把 `PDF附件` 入口放在 `网页快照` 旁边。
- 中文路径在 PowerShell 中有时会显示乱码，但文件本身是 UTF-8。遇到中文路径读取问题，可用 Python `Path.iterdir()`/`glob()` 动态找目录，避免硬编码中文路径。
- PowerShell 不支持 Bash 风格 `python - <<'PY'`，请用：

```powershell
@'
print("hello")
'@ | D:\miniconda\envs\py311\python.exe -
```

## 常用命令

检查工作区：

```powershell
git status --short
git log -5 --oneline
```

脚本语法自检：

```powershell
D:\miniconda\envs\py311\python.exe -m compileall -q scripts
```

记录日志：

```powershell
D:\miniconda\envs\py311\python.exe scripts\log_action.py --type "操作类型" --content "做了什么" --files "涉及文件" --command "运行命令" --result "完成"
```

重建搜索页：

```powershell
D:\miniconda\envs\py311\python.exe scripts\09_build_search_page.py
```

全文检索自检：

```powershell
D:\miniconda\envs\py311\python.exe scripts\07_search.py --self-check-only
```

下载/重建 PDF 附件清单：

```powershell
D:\miniconda\envs\py311\python.exe scripts\20_download_pdf_attachments.py --delay 0 --timeout 30
```

抽取 PDF 文本：

```powershell
D:\miniconda\envs\py311\python.exe scripts\14_extract_pdf_texts.py --ocr --max-pages 0
```

生成网页快照：

```powershell
D:\miniconda\envs\py311\python.exe scripts\19_build_web_snapshots.py --force
```

只更新某条快照：

```powershell
D:\miniconda\envs\py311\python.exe scripts\19_build_web_snapshots.py --only-id AUTO-XXXX --force --update-existing-manifest
```

生成知识切片：

```powershell
D:\miniconda\envs\py311\python.exe scripts\16_build_knowledge_chunks.py
```

构建 RAG 索引：

```powershell
D:\miniconda\envs\py311\python.exe scripts\17_build_rag_index.py
```

RAG 查询：

```powershell
D:\miniconda\envs\py311\python.exe scripts\18_rag_query.py "四川电力辅助服务市场交易实施细则有哪些主要依据"
```

15天自动更新周期：

```powershell
D:\miniconda\envs\py311\python.exe scripts\21_run_update_cycle.py --full-auto --strict-gate --target-count 600 --pages 25 --timeout 20 --delay 0.5 --min-score 55 --skip-snapshots --skip-ocr
```

本地问答 API：

```powershell
D:\miniconda\envs\py311\python.exe scripts\22_qa_service.py
```

## 主要脚本速查

- `01_fetch_seed_urls.py`：按 `02_元数据/待采集链接.csv` 定点采集入库。
- `02_collect_official_candidates.py`：从来源入口发现候选政策链接。
- `03_evaluate_retrieval.py`：检索效果评测。
- `04_export_excel_workbook.py`：导出 Excel 工作簿。
- `05_sync_excel_to_csv.py`：把 Excel 修改同步回 CSV。
- `06_backfill_candidates_to_seed.py`：把候选链接回填到待采集链接。
- `07_search.py`：命令行全文检索和自检。
- `08_prepare_review_sample.py`：生成核验样本。
- `09_build_search_page.py`：生成本地搜索页和 `search_index.json`。
- `10_audit_titles.py`：标题清洗建议。
- `11_backfill_source_org.py`：回填细化来源机构。
- `12_doc_number_and_rule_list.py`：提取文号并生成规则清单。
- `13_build_policy_relations.py`：根据明文引用建立新旧政策关系。
- `14_extract_pdf_texts.py`：抽取/OCR PDF 文本并回填台账。
- `15_mass_crawl_candidates.py`：批量候选发现和评分。
- `16_build_knowledge_chunks.py`：知识切片。
- `17_build_rag_index.py`：构建 RAG 检索索引。
- `18_rag_query.py`：RAG 问答。
- `19_build_web_snapshots.py`：生成截图式网页快照。
- `20_download_pdf_attachments.py`：发现并下载网页中的 PDF 附件。
- `21_run_update_cycle.py`：15天全链路更新周期，输出更新报告。
- `22_qa_service.py`：FastAPI 本地问答服务，提供 `/api/health` 和 `/api/ask`。
- `23_validate_knowledge_base.py`：数据验收，检查台账规模、路径、文本、切片覆盖和附件失败项。
- `24_prepare_candidate_review_queue.py`：把 50-54 分低分候选输出到人工复核队列。
- `domain_terms.py`：统一主题词、候选关键词、RAG 已知词和主题推断规则。
- `llm_client.py`：OpenAI-compatible 大模型接口适配层。
- `log_action.py`：写实验日志。

## 标准流水线

1. 维护来源清单：细化到国家级、省级、直辖市、区域监管局、交易中心、电网企业等。
2. 批量发现候选：运行 `15_mass_crawl_candidates.py` 或定向采集脚本。
3. 回填待采集链接：把高质量候选写入 `02_元数据/待采集链接.csv`。
4. 定点入库：运行 `01_fetch_seed_urls.py`，写入原始资料、处理后文本、政策资料台账。
5. 清洗增强：运行标题清洗、来源机构回填、文号提取、规则清单、政策关联关系脚本。
6. 下载 PDF 附件：运行 `20_download_pdf_attachments.py`。
7. 抽取 PDF 文本：运行 `14_extract_pdf_texts.py`，必要时 OCR。
8. 生成网页快照：运行 `19_build_web_snapshots.py`。
9. 生成搜索页：运行 `09_build_search_page.py`。
10. 生成知识切片和 RAG 索引：运行 `16_build_knowledge_chunks.py`、`17_build_rag_index.py`。
11. 验证：运行 `compileall`、`07_search.py --self-check-only`、`18_rag_query.py --self-check`，必要时做 RAG 查询。
12. 记录日志并提交推送。

自动化场景优先运行 `21_run_update_cycle.py`，它会串联候选发现、严格分流、入库、附件、文本抽取、切片、索引、搜索页和自检，并生成 `06_实验日志/更新报告_YYYYMMDD.md`。

## 搜索页现状

`search.html` 是当前最直观的交互入口。结果卡片中应包含：

- 原文链接
- 网页快照
- PDF 附件
- 文号
- 发布部门/采集来源机构/发布日期
- 快照类型标签，例如 `官网长截图`、`模拟截图`
- 明文引用和被后续引用关系

如果后续改搜索页，请注意：

- 不要展示难看的本地长路径作为主文本。
- 路径应放在链接 `href` 或悬停提示里。
- 页面里已经不再使用“区域筛选”为主，核心筛选按省份和主题。

## 后续优先任务

建议下一步优先做：

1. 从 `05_输出成果/候选人工复核队列_20260502.csv` 人工确认一批 50-54 分正式规则，确认后再回填入库，把主库推进到 500-600 条。
2. 继续扩源，优先补国家能源局政府信息公开、各省能源局/发改委稳定列表页、交易中心公开规则页。
3. 对剩余 4 个 PDF 附件失败项做人工核验，确认是否为历史坏链或需替换来源。
4. 接入真实 `LLM_API_KEY` 后，用 `22_qa_service.py` 做问答验收。
5. 公众号、财经新闻、咨询机构解读资料只放辅助层，需和官方政策分层标识，默认不进入主问答索引。

## Git 提交流程

完成一个闭环后：

```powershell
git status --short
git diff --check
git add <相关文件>
git commit -m "简短英文提交信息"
git push origin main
```

提交前请特别注意：

- 单个文件不要超过 GitHub 100MB 限制。
- PDF、截图这类大文件会显著增加仓库体积，提交前先看大小。
- 如果只是 dry-run 产物，不要提交。
