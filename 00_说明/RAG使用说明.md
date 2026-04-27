# 本地 RAG 使用说明

当前 RAG 是本地离线原型，不调用外部大模型 API。

## 当前能力

- 从 `02_元数据/知识切片表.csv` 构建本地检索索引。
- 查询时先做向量相似度检索，再结合关键词命中、权威等级、检索权重重排。
- 输出带引用的回答草稿，引用包含文件标题、发布机构、发布日期、文号、原文链接和正文摘录。

## 构建索引

```powershell
D:\miniconda\envs\py311\python.exe .\scripts\17_build_rag_index.py
```

输出：

- `05_输出成果/rag_index.pkl.gz`
- `05_输出成果/rag_index.summary.json`

## 查询示例

```powershell
D:\miniconda\envs\py311\python.exe .\scripts\18_rag_query.py --query "四川电力辅助服务市场交易实施细则有哪些主要依据" --region 四川 --write
```

输出会保存为：

- `05_输出成果/rag回答_*.md`

## 自检

```powershell
D:\miniconda\envs\py311\python.exe .\scripts\18_rag_query.py --self-check
```

## 使用原则

- 官方政策、监管规则、交易规则优先作为依据。
- 公众号、咨询机构、财经新闻后续可以入库，但默认不参与正式依据排序。
- 当前回答是“检索增强草稿”，适合核对来源和摘录；后续可在此基础上接入大模型生成正式回答。
