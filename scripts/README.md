# scripts

当前脚本目录已经包含两条可用链路：

```text
01_fetch_seed_urls.py
  从 待采集链接.csv 读取人工确认过的种子链接
  下载原始文件
  提取网页文本
  写入政策资料台账

02_collect_official_candidates.py
  从 来源清单.csv 读取白名单来源
  扫描可自动采集的 HTML列表 / JSON列表 / 部分公开平台
  按关键词筛出候选政策链接
  输出到 05_输出成果

log_action.py
  追加结构化实验日志

03_evaluate_retrieval.py
  读取 问题评测表.csv 和 政策资料台账.csv
  对前 N 个评测问题执行简易关键词检索
  输出 Top3 命中结果到 05_输出成果
  自检表头、基准答案完整性、重复编号和内置排序样例

04_export_excel_workbook.py
  将 02_元数据 中的核心 CSV 和最新检索评测结果汇总为本地 Excel 工作簿
  输出到 02_元数据/知识库管理工作簿.xlsx
  自动设置筛选、冻结首行、列宽和基础表头样式
  生成后会读回工作簿做自检

05_sync_excel_to_csv.py
  将 02_元数据/知识库管理工作簿.xlsx 中的可管理工作表同步回对应 CSV
  默认只做 dry-run 预检查，不写文件
  使用 --write 时会先在 02_元数据/备份 下备份原CSV，再写回
  会校验表头、主键字段和重复编号

06_backfill_candidates_to_seed.py
  将 来源清单候选链接_*.csv 中的高价值候选回填到 待采集链接.csv
  默认只预览，使用 --write 时才写入

07_search.py
  对 政策资料台账.csv 和 03_处理后文本 执行本地全文检索
  支持地区、主题、来源类型过滤
  输出全文检索结果到 05_输出成果

08_prepare_review_sample.py
  从 政策资料台账.csv 生成首轮人工核验样本
  默认只预览，使用 --write 时写入 人工核验表.csv

09_build_search_page.py
  从 政策资料台账.csv 和 03_处理后文本 生成 search_index.json
  同时生成可直接打开使用的 search.html
  HTML 页面内嵌一份索引数据，可在无本地服务时使用

10_audit_titles.py
  扫描 政策资料台账.csv 中的泛标题、栏目标题、附件编号标题
  输出 标题清洗建议_YYYYMMDD.csv
  默认不修改台账；使用 --apply-safe 时只应用高置信修正，并先备份台账

11_backfill_source_org.py
  为 政策资料台账.csv 新增并回填 采集来源机构 字段
  优先从 待采集链接.csv 的 来源名称 回填，缺失时使用 发布部门
  写回前会自动备份原台账

12_doc_number_and_rule_list.py
  为 政策资料台账.csv 新增/回填 文号 字段
  生成 02_元数据/规则清单.csv
  支持 --rebuild-doc-no 重新计算文号，写回前会自动备份原台账

13_build_policy_relations.py
  生成 02_元数据/政策关联关系表.csv
  只在新政策明文出现旧政策文号或准确规则名称时建立关系
  不做标题相似度推断，避免误连

14_extract_pdf_texts.py
  尝试从已下载 PDF 中抽取文本
  支持 --ocr 对扫描件 PDF 做 OCR
  支持 --sync-existing 将已经生成的 .txt/.ocr.txt 挂回台账

15_mass_crawl_candidates.py
  基于 来源清单.csv 做大规模候选池采集
  输出 大规模候选池_YYYYMMDD.csv 和 summary.json
  标注质量分、入库状态和建议动作，避免低质量网页直接进入正式台账

16_build_knowledge_chunks.py
  从 03_处理后文本 生成 02_元数据/知识切片表.csv
  同步输出 05_输出成果/knowledge_chunks.jsonl
  用于后续 RAG/向量数据库入库

17_build_rag_index.py
  从 02_元数据/知识切片表.csv 构建本地 RAG 检索索引
  当前使用 TF-IDF 字符 2-4gram，离线运行，不依赖外部 API
  输出 05_输出成果/rag_index.pkl.gz 和 rag_index.summary.json

18_rag_query.py
  查询本地 RAG 索引
  按向量相似度、关键词命中、权威等级、检索权重重排
  生成带来源标题、机构、日期、文号、原文链接的回答草稿

19_build_web_snapshots.py
  为已入库网页生成离线快照索引
  用于后续复核、归档和离线查看

20_download_pdf_attachments.py
  扫描台账中的政策网页附件
  下载 PDF 到 01_原始资料/PDF附件
  同步生成 PDF附件清单.csv 和 pdf_attachments.json

21_run_update_cycle.py
  15 天更新周期入口
  串联候选发现、严格分流、自动入库、附件下载、文本抽取、切片、索引、搜索页和自检
  生成 06_实验日志/更新报告_YYYYMMDD.md

22_qa_service.py
  本地问答 API 服务
  提供 GET /api/health 和 POST /api/ask
  使用 OpenAI-compatible 大模型接口生成带引用回答；未配置 API Key 时回退到本地 RAG 草稿

domain_terms.py
  统一维护主题词、候选发现关键词、RAG 已知词和主题推断规则

llm_client.py
  读取 .env / 环境变量
  调用 OpenAI-compatible chat completions 接口
```

常用命令：

```powershell
D:\miniconda\envs\py311\python.exe .\scripts\03_evaluate_retrieval.py --self-check-only
D:\miniconda\envs\py311\python.exe .\scripts\03_evaluate_retrieval.py --limit 10
D:\miniconda\envs\py311\python.exe .\scripts\04_export_excel_workbook.py
D:\miniconda\envs\py311\python.exe .\scripts\05_sync_excel_to_csv.py
D:\miniconda\envs\py311\python.exe .\scripts\05_sync_excel_to_csv.py --write
D:\miniconda\envs\py311\python.exe .\scripts\06_backfill_candidates_to_seed.py --limit 12 --write
D:\miniconda\envs\py311\python.exe .\scripts\07_search.py 山东 辅助服务 结算 --region 山东 --top-k 5
D:\miniconda\envs\py311\python.exe .\scripts\08_prepare_review_sample.py --limit 20 --write
D:\miniconda\envs\py311\python.exe .\scripts\09_build_search_page.py
D:\miniconda\envs\py311\python.exe .\scripts\10_audit_titles.py
D:\miniconda\envs\py311\python.exe .\scripts\11_backfill_source_org.py
D:\miniconda\envs\py311\python.exe .\scripts\12_doc_number_and_rule_list.py --rebuild-doc-no
D:\miniconda\envs\py311\python.exe .\scripts\13_build_policy_relations.py
D:\miniconda\envs\py311\python.exe .\scripts\14_extract_pdf_texts.py --ocr
D:\miniconda\envs\py311\python.exe .\scripts\15_mass_crawl_candidates.py --pages 20 --timeout 15
D:\miniconda\envs\py311\python.exe .\scripts\16_build_knowledge_chunks.py --max-chars 450 --overlap 60
D:\miniconda\envs\py311\python.exe .\scripts\17_build_rag_index.py
D:\miniconda\envs\py311\python.exe .\scripts\18_rag_query.py --query "四川电力辅助服务市场交易实施细则有哪些主要依据" --region 四川 --write
D:\miniconda\envs\py311\python.exe .\scripts\21_run_update_cycle.py --full-auto --strict-gate --target-count 600 --pages 25 --timeout 20 --delay 0.5 --min-score 55 --skip-snapshots --skip-ocr
D:\miniconda\envs\py311\python.exe .\scripts\22_qa_service.py
```

注意：不要在 `04_export_excel_workbook.py` 仍在生成工作簿时同时运行 `05_sync_excel_to_csv.py`，否则同步脚本可能读到未写完的 Excel 文件。

问答服务启动后，打开 `05_输出成果/search.html`，右侧问答区会调用 `http://127.0.0.1:8000/api/ask`。大模型参数从 `.env` 或环境变量读取：`LLM_PROVIDER`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_TEMPERATURE`、`LLM_MAX_CONTEXT_CHUNKS`。
