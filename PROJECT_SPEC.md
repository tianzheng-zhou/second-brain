# PersonalBrain 项目完整规格文档

> 本文档用于重构参考，完整描述系统的所有功能、数据模型、架构和实现细节。
> 更新日期：2026-03-06

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [技术栈](#2-技术栈)
3. [目录结构](#3-目录结构)
4. [架构概述](#4-架构概述)
5. [数据模型（数据库）](#5-数据模型数据库)
6. [配置系统](#6-配置系统)
7. [核心模块详解](#7-核心模块详解)
8. [MCP 服务器](#8-mcp-服务器)
9. [管理后台](#9-管理后台)
10. [CLI 工具](#10-cli-工具)
11. [外部依赖与服务](#11-外部依赖与服务)
12. [已知设计约束](#12-已知设计约束)

---

## 1. 项目定位与目标

PersonalBrain（PB）是一个个人知识库**记忆后端**，核心理念是"扔进即忘，需时即查"。

**系统定位：**
- 纯后端服务，通过 MCP 协议对外提供记忆存取能力
- 不包含对话前端，Agent 智能由 MCP 客户端（Claude Desktop、Cursor 等）承担
- 附带 Streamlit 管理后台，用于数据管理和系统配置

**核心能力：**
- **存储**：支持文本、PDF、图片、音频等多模态文件入库，自动提取文本、生成摘要和向量索引
- **检索**：混合搜索（向量语义搜索 + FTS5 全文检索），支持时间过滤和类型过滤
- **溯源**：搜索结果可追溯到源文件及其在文档中的位置，支持读取源文件的处理后版本（如 PDF → Markdown）
- **管理**：笔记的增删改查、文件管理、索引维护、异步入库与进度查询

---

## 2. 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| MCP 服务 | mcp (FastMCP) |
| 管理后台 | Streamlit |
| 数据库 | SQLite + sqlite-vec（向量索引扩展） |
| LLM/Embedding | 阿里云百炼 DashScope（兼容 OpenAI 接口） |
| LLM 客户端 | openai SDK |
| PDF 解析 | MinerU API（云端，复杂 PDF）/ 本地降级方案 |
| 音频转写 | DashScope ASR（qwen3-asr-flash-filetrans） |
| 文件云存储 | 阿里云 OSS（用于 MinerU/ASR 中转） |
| 环境变量 | python-dotenv |
| 时间解析 | dateparser |
| 重试 | tenacity |


**默认模型配置（可通过 config_manager 动态修改）：**
```
embedding_model: qwen3-vl-embedding (2560维)
rerank_model:    qwen3-vl-rerank
vision_model:    kimi-k2.5
enrichment_model: kimi-k2.5        (摘要/实体提取)
semantic_split_model: qwen3.5-flash
```
注意：阿里云百炼同样提供了kimi-k2.5的api服务


---

## 3. 目录结构

```
second-brain/
├── personal_brain/              # 核心业务包
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                   # Click CLI 工具
│   ├── config.py                # 环境变量加载（凭证、路径）
│   ├── core/
│   │   ├── config_manager.py    # 统一配置管理（环境变量 > JSON > 默认值）
│   │   ├── database.py          # 所有数据库操作函数
│   │   ├── enrichment.py        # 文件自动摘要/实体提取（依赖 LLM）
│   │   ├── indexer.py           # 文本提取、分块、Embedding 生成（依赖 LLM）
│   │   ├── ingestion.py         # 文件入库主流程
│   │   ├── llm.py               # LLM 调用封装（供 enrichment/indexer 使用）
│   │   ├── models.py            # Pydantic 数据模型
│   │   ├── reranker.py          # 重排序（DashScope REST API）
│   │   └── search.py            # 语义搜索主函数
│   └── utils/
│       ├── aliyun_oss.py        # 阿里云 OSS 文件操作
│       ├── asr_client.py        # 音频转写客户端
│       ├── file_ops.py          # 文件类型识别、hash、存储组织
│       └── mineru.py            # MinerU PDF 解析 API 客户端
├── mcp_server.py                # MCP 服务器入口（stdio/SSE/StreamableHTTP）
├── admin_app.py                 # Streamlit 管理后台
├── .env / .env.example          # 环境变量
├── pyproject.toml               # 项目配置与依赖
└── data/
    ├── brain.db                 # 主数据库（默认位置，可被 PB_DB_PATH 覆盖）
    ├── model_config.json        # 运行时模型配置持久化
    ├── processed/               # 处理后的可读文本（PDF→MD、图片→描述等）
    ├── mineru_cache/            # MinerU 解析结果缓存
    └── uploads/                 # 临时上传文件
```

---

## 4. 架构概述

系统分为三个层次：

```
┌──────────────────────────────────────────────────────┐
│               External Interface                      │
│  ┌──────────────────────────────┐  ┌──────────────┐  │
│  │     MCP Server               │  │  Streamlit   │  │
│  │  (stdio / SSE / StreamHTTP)  │  │  (Admin UI)  │  │
│  └──────────────┬───────────────┘  └──────┬───────┘  │
└─────────────────┼────────────────────────┼───────────┘
                  │                        │
┌─────────────────▼────────────────────────▼───────────┐
│               Core Business Logic                     │
│  search.py / ingestion.py / enrichment.py             │
│  indexer.py / reranker.py                             │
│  llm.py ← config_manager.py                          │
└─────────────────────────┬────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────┐
│               Storage Layer                           │
│  brain.db (SQLite + sqlite-vec)                       │
│  File System (STORAGE_PATH/YYYY-MM/)                  │
└──────────────────────────────────────────────────────┘
```

**数据流（文件入库）：**

MCP `ingest_file` 支持异步模式：立即返回 `task_id`，后台执行入库流程，客户端通过 `get_task_status` 查询进度。CLI 和 Admin 仍为同步模式。

```
MCP ingest_file(async) / CLI ingest / Admin 上传
    → 创建 task 记录（status=pending）
    → ingestion.process_file()
        → calculate_file_id() [SHA256 前16位，去重]
        → organize_file() [复制到 STORAGE_PATH/YYYY-MM/]
        → extract_text() [根据类型选择解析器]
            → PDF: MinerU API → 提取MD → 保存到 processed/{file_id}.md
                   降级: 本地 Pillow 转图片 → vision model OCR
            → IMAGE: Vision Model 多模态理解（OCR + 内容描述）→ 保存到 processed/{file_id}.md
            → AUDIO: 上传OSS → ASR API 轮询 → 删除OSS临时文件 → 保存到 processed/{file_id}.md
            → TEXT/MD/CODE: 直接读取（无需 processed 副本）
        → save_file() [写入 files 表，含 processed_text_path]
        → generate_embedding_chunks() [分块 + 批量 Embedding + 记录位置信息]
        → save_chunks() [写入 file_chunks + vec_items + fts_chunks]
        → enrich_file() [摘要 + 标签提取 + 保存 entry]
    → 更新 task 记录（status=completed / failed）
```

**数据流（混合搜索）：**
```
MCP search / CLI search
    → search_files(query, limit, ...)
        → 向量分支: generate_embedding(query) → vec_items KNN
        → 全文分支: fts_chunks FTS5 MATCH → BM25 评分
        → 融合去重 + 归一化评分
        → 可选 Rerank (qwen3-vl-rerank)
        → 返回结果列表（含溯源信息：source_file_id, chunk_index, page_number）
```

---

## 5. 数据模型（数据库）

### brain.db（位于 STORAGE_PATH/brain.db）

#### `files` 表
文件元数据，每个入库文件对应一条记录。
```sql
id                  TEXT PRIMARY KEY  -- SHA256[:16]，基于内容哈希，天然去重
path                TEXT UNIQUE       -- 原始文件在 STORAGE_PATH 的绝对路径
processed_text_path TEXT              -- 处理后的文本版本路径（PDF→MD、图片→描述文本等），供客户端溯源阅读
filename            TEXT
type                TEXT              -- image / audio / text / pdf / unknown
size_bytes          INTEGER
created_at          TIMESTAMP
last_accessed       TIMESTAMP
status              TEXT              -- active / archived / deleted
```

> 全文内容不再存储在数据库中，而是通过 `processed_text_path` 指向文件系统中的处理后文本，避免大文件膨胀数据库。

#### `vec_items` 虚拟表（sqlite-vec）
统一向量索引，通过 `source_type` + `source_id` 直接标识来源，无需额外映射表。
```sql
CREATE VIRTUAL TABLE vec_items USING vec0(
    embedding float[2560],
    source_type TEXT,   -- 'chunk' / 'entry'
    source_id   TEXT    -- 对应 file_chunks.id 或 entries.id
)
```

> 搜索时直接从 vec_items 获取 source_type + source_id，JOIN 到对应表取内容，避免 N+1 查询。

#### `file_chunks` 表
文件分块内容，含位置信息用于溯源。
```sql
id          TEXT PRIMARY KEY  -- "{file_id}_{chunk_index}"
file_id     TEXT
chunk_index INTEGER
content     TEXT
start_char  INTEGER           -- 在原文中的起始字符偏移
page_number INTEGER           -- 所在页码（仅 PDF 有值，其他为 NULL）
```

#### `entries` 表
核心记忆/笔记，用户主动写入或 enrichment 自动生成的摘要。
```sql
id           TEXT PRIMARY KEY  -- UUID
content_text TEXT              -- 主文本内容
metadata     TEXT              -- JSON，附加元数据（description 等，不含文件关联）
created_at   TIMESTAMP
source       TEXT              -- mcp / auto_enrichment / cli 等
tags         TEXT              -- JSON 数组字符串
status       TEXT              -- active / archived / deleted
```

> 文件关联只通过 `entry_files` 表维护，不在 entries 内部重复存储。
> 去掉 `entry_type`（可从 entry_files 是否有记录推导）、`importance`、`trash_score`（简化为 status 管理）。

#### `entry_files` 表（多对多关联）
笔记与文件的关联关系。
```sql
entry_id TEXT
file_id  TEXT
PRIMARY KEY (entry_id, file_id)
```

#### `fts_chunks` 虚拟表（FTS5 全文检索）
用于关键词精确匹配，与向量搜索互补形成混合检索。
```sql
CREATE VIRTUAL TABLE fts_chunks USING fts5(
    chunk_id,       -- 对应 file_chunks.id
    content,        -- 分块文本内容（与 file_chunks.content 同步）
    tokenize='unicode61'
)
```

#### `tasks` 表
异步任务状态追踪（用于大文件入库等耗时操作）。
```sql
id          TEXT PRIMARY KEY  -- UUID
type        TEXT              -- ingest_file / refresh_index
status      TEXT              -- pending / running / completed / failed
file_path   TEXT              -- 输入路径
result_json TEXT              -- 完成后的结果（file_id 等）或错误信息
created_at  TIMESTAMP
updated_at  TIMESTAMP
```

---

## 6. 配置系统

统一配置入口 `config_manager.py`，单一读取接口 `config.get(key)`。

**优先级：环境变量 > model_config.json > 代码默认值**

### 6.1 环境变量（.env）

仅用于凭证和路径等部署相关配置，不包含模型名等业务配置。

| 变量 | 含义 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key | 必填 |
| `DASHSCOPE_BASE_URL` | DashScope 兼容 OpenAI 端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `PB_STORAGE_PATH` | 数据存储目录 | `~/personal_brain_data` |
| `PB_DB_PATH` | 数据库文件路径 | `{STORAGE_PATH}/brain.db` |
| `ALIYUN_ACCESS_KEY_ID` | 阿里云 AccessKey ID | 可选（PDF/音频处理需要） |
| `ALIYUN_ACCESS_KEY_SECRET` | 阿里云 AccessKey Secret | 可选 |
| `ALIYUN_OSS_ENDPOINT` | OSS 端点 | `oss-cn-hangzhou.aliyuncs.com` |
| `ALIYUN_OSS_BUCKET` | OSS Bucket 名 | 可选 |
| `MINERU_API_TOKEN` | MinerU API Token | 可选（PDF 解析） |
| `MINERU_BASE_URL` | MinerU API 地址 | `https://mineru.net/api/v4` |

### 6.2 业务配置（model_config.json）

持久化到 `{STORAGE_PATH}/model_config.json`，可通过管理后台或 MCP 修改。

**默认值：**
```json
{
  "embedding_model": "qwen3-vl-embedding",
  "rerank_model": "qwen3-vl-rerank",
  "vision_model": "kimi-k2.5",
  "enrichment_model": "kimi-k2.5",
  "embedding_batch_size": 6,
  "use_semantic_split": false,
  "semantic_split_model": "qwen3.5-flash",
  "chunk_size": 1500,
  "chunk_overlap": 200
}
```

> 注意：阿里云百炼同样提供了 kimi-k2.5 的 API 服务。

---

## 7. 核心模块详解

### 7.1 indexer.py — 文本提取与 Embedding

**文本提取（`extract_text(path, file_type)`）：**

| 文件类型 | 提取方式 | 返回 |
|----------|----------|------|
| PDF | MinerU API（优先）→ 降级到本地 Pillow+Vision OCR | `(markdown_text, image_root_path)` |
| IMAGE | base64 编码 + Vision Model 多模态理解（OCR + 内容描述） | `(text, None)` |
| AUDIO | 上传 OSS → ASR API → 清理 OSS | `(transcript_text, None)` |
| TEXT/MD/CODE | 直接读取文件内容 | `(text, None)` |

**PDF 处理降级策略（`_extract_pdf_with_fallback`）：**
1. 尝试 MinerU API（上传 OSS → 提交任务 → 轮询状态 → 下载 ZIP → 提取 MD 文件）
2. MinerU 失败或不可用 → 本地用 Pillow 将 PDF 每页转为图片 → Vision Model OCR

**MinerU 缓存策略：**
- 缓存目录：`{STORAGE_PATH}/mineru_cache/{file_hash_prefix}/`
- 每次先检查缓存，命中则跳过 API 调用
- 缓存内容：原始 PDF 副本、content_list JSON、Markdown 全文、layout JSON

**分块策略（`generate_embedding_chunks`）：**

1. **简单分块（默认，`USE_SEMANTIC_SPLIT=false`）：**
   - 按字符数分割，支持 overlap
   - 先按段落（双换行）分，超过 CHUNK_SIZE 则强制截断
   - 记录每个 chunk 的 `start_char` 偏移量和 `page_number`（PDF 通过页面标记推算）

2. **语义分块（`USE_SEMANTIC_SPLIT=true`）：**
   - 使用 `semantic_text_splitter()`
   - 将文本切成段落/图片 token，发给 LLM 识别语义分割点
   - 支持图片嵌入：Markdown 图片语法 `![](path)` 被转换为 base64 注入 LLM
   - 每批最多 30 个段落，超出则滑动窗口处理
   - Chunk 大小范围：目标 CHUNK_SIZE 的 30%~150%
   - 同样记录 `start_char` 和 `page_number`

**图片内容理解（`extract_image_content`）：**
- Vision Model 同时执行 OCR（文字提取）和内容描述（场景、物体、图表含义等）
- 输出合并为一段结构化文本，用于后续 Embedding 和搜索

**处理后文本持久化：**
- PDF：MinerU 解析生成的 Markdown 文件保存到 `{STORAGE_PATH}/processed/{file_id}.md`
- 图片：Vision Model 生成的描述文本保存到 `{STORAGE_PATH}/processed/{file_id}.md`
- 音频：ASR 转写文本保存到 `{STORAGE_PATH}/processed/{file_id}.md`
- 文本类文件：无需处理，原文件即可直接阅读
- 路径记录到 `files.processed_text_path`，供 MCP `read_document` 返回给客户端

**Embedding 生成（`generate_embedding`）：**
- 使用 DashScope `qwen3-vl-embedding` 模型
- 支持文本和多模态（含图片的 chunk）
- 多模态消息格式：图片转 base64 + text 混合
- 批量处理，`embedding_batch_size=2`（默认）
- 使用 tenacity 重试（最多3次，指数退避）

**FTS5 索引同步：**
- 每个 chunk 写入 `file_chunks` 时同步写入 `fts_chunks`
- 删除/重建索引时同步清理 FTS5 数据

### 7.2 ingestion.py — 文件入库流程

**`process_file(file_path)` 完整流程：**
1. 计算文件内容 SHA256[:16] 作为 `file_id`
2. 查询 DB，已存在则直接返回（去重）
3. `organize_file()`：将文件复制到 `STORAGE_PATH/YYYY-MM/filename`（同名不同内容自动重命名）
4. 确定文件类型（扩展名优先）
5. `extract_text()`：提取文本内容，保存处理后文本到 `processed/`
6. `save_file()`：写入 files 表（含 processed_text_path）
7. 生成 Embedding chunks 并保存（所有文件均索引）
8. `enrich_file()`：自动摘要 + 标签提取

**`refresh_index_for_file(file_id)` 重建索引：**
1. 重新提取文本，更新 processed 文件
2. 删除旧 chunks/embeddings/FTS 数据
3. 重新生成 chunks + embeddings
4. 重新运行 `enrich_file()`

### 7.3 search.py — 混合搜索

采用**向量语义搜索 + FTS5 全文检索**的混合策略，兼顾语义理解和精确关键词匹配。

**`search_files(query, limit, use_rerank, time_range, entry_type, file_id)`：**

1. **向量搜索分支**：
   - 生成查询 Embedding
   - 在 `vec_items` 表执行 KNN（sqlite-vec MATCH）
   - 初始候选量：`max(100, limit * 20)`
   - 通过 `source_type` + `source_id` 直接 JOIN 到 file_chunks / entries
2. **全文检索分支**：
   - 在 `fts_chunks` 表执行 FTS5 MATCH 查询
   - 返回 BM25 评分的候选列表
3. **结果融合**：
   - 合并两路结果，去重（同一 chunk 取较高分）
   - 统一归一化评分
4. 按 `source_type` 过滤（chunk / entry）
5. 按时间范围过滤（`time_range` 元组）
6. 可选 Rerank（`qwen3-vl-rerank`，via REST API）
7. 返回候选列表，每条结果包含：
   - `score`：最终评分
   - `source_file_id`：来源文件 ID
   - `source_filename`：来源文件名
   - `chunk_index`：在文件中的 chunk 序号
   - `page_number`：所在页码（PDF 文件）
   - `content`：匹配的文本片段

### 7.5 enrichment.py — 自动摘要与标签提取

**`enrich_file(file_obj, text, chunks, embeddings)`：**

**摘要生成策略（依赖 LLM）：**
- 估算 token 数（CJK: 1.2/字，其他: 0.35/字，图片: 1000/张）
- `token <= 20000`：全文直接发给 LLM 生成摘要
- `token > 20000`：选取代表性 chunk（首/尾 + 均匀间隔共10个），生成摘要

**标签提取（依赖 LLM）：**
- 从摘要文本（不是全文）提取 3-5 个 tags
- 将摘要保存为新 entry（source="auto_enrichment"），通过 `entry_files` 关联源文件

### 7.6 reranker.py — 重排序

调用 DashScope 的 REST API（非 SDK），模型 `qwen3-vl-rerank`。

- 单文档最大长度：8000 字符（超出截断）
- 支持 `top_n` 参数
- 失败时返回 score=0 的原始顺序（不崩溃）

### 7.7 llm.py — LLM 调用

- 使用 openai SDK，base_url 指向 DashScope 兼容端点
- 供 enrichment（摘要/实体提取）和 indexer（语义分块）使用
- 自动识别 `qwen3.5-flash` 系列"思考模型"，默认关闭 `enable_thinking`
- 模型从 `config_manager` 动态读取

---

## 8. MCP 服务器

文件：`mcp_server.py`，基于 `FastMCP`。

**MCP 是系统的主要对外接口**，所有记忆存取操作都通过 MCP 工具暴露给客户端。

### 8.1 启动方式

| 传输方式 | 命令 | 说明 |
|----------|------|------|
| Stdio | `python mcp_server.py --transport stdio` | 本地客户端（Claude Desktop 等） |
| SSE | `python mcp_server.py --transport sse --port 8000` | 远程客户端，SSE 端点 |
| Streamable HTTP | `python mcp_server.py --transport streamable-http --port 8000` | 远程客户端，HTTP 流式 |

### 8.2 MCP 工具集

#### 搜索与检索

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `search` | `query: str, limit: int = 5, time_range?: str, source_type?: str` | 混合搜索（向量+全文），返回结果含溯源信息（source_file_id, chunk_index, page_number） |
| `read_note` | `entry_id: str` | 按 ID 读取某条笔记的完整内容 |
| `read_document` | `file_id: str, query?: str` | 读取文件的处理后文本（PDF→MD、图片→描述等）；大文件可带 query 做局部检索 |
| `list_notes` | `tag?: str, source?: str, limit?: int, offset?: int` | 列出笔记，支持按 tag/来源过滤和分页 |
| `list_files` | `type?: str, status?: str, limit?: int, offset?: int` | 列出文件，支持按类型/状态过滤和分页 |
| `get_file_info` | `file_id: str` | 获取文件元信息（大小、类型、创建时间、tags、摘要等，不含全文） |

#### 写入与修改

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `write_note` | `content: str, tags?: list[str], file_paths?: list[str]` | 写入笔记/记忆，可附带文件（触发 ingest） |
| `update_note` | `entry_id: str, content?: str, tags?: list[str]` | 修改笔记内容或 tags，重新生成 Embedding |
| `delete_note` | `entry_id: str` | 删除笔记 |
| `ingest_file` | `path: str, async?: bool = true` | 导入文件或目录；默认异步，返回 task_id |
| `delete_file` | `file_id: str` | 删除文件及其关联的 chunks/embeddings/FTS 索引 |

#### 系统管理

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `get_stats` | 无 | 知识库统计概览（文件数、笔记数、存储大小等） |
| `refresh_index` | `file_id: str` | 重建某文件的索引（重新提取文本、分块、Embedding） |
| `get_task_status` | `task_id: str` | 查询异步任务状态和结果（入库进度、成功/失败等） |

> MCP 工具是无状态的。对话历史、上下文管理、Agent 循环均由 MCP 客户端负责。

---

## 9. 管理后台

文件：`admin_app.py`，基于 Streamlit。

用于系统管理，不涉及对话功能。

**Ingest（导入页）：**
- 从路径导入（文件或文件夹）
- 上传文件导入（支持多文件）
- 支持格式：`png/jpg/jpeg/webp/gif/mp3/wav/ogg/m4a/pdf/txt/md/markdown/json/csv/py/js/html/css/yaml/yml/xml`

**Manage（管理页）：**
- 数据库状态显示
- 初始化数据库按钮
- 重置数据库（删除并重建，带二次确认）
- 扫描并自动索引遗漏文件
- 文件列表（DataFrame 显示）
- 单文件操作：刷新索引 / 删除 / 查看详情 JSON

**Config（配置页）：**
- 查看/修改运行时模型配置
- 数据库 Schema 查看（Debug 用）

---

## 10. CLI 工具

命令：`python -m personal_brain.cli <command>`

| 命令 | 参数 | 功能 |
|------|------|------|
| `init` | 无 | 初始化数据库和目录 |
| `reset` | 无（带二次确认） | 删除并重建数据库 |
| `ingest <path>` | 文件或目录路径 | 批量导入 |
| `search <query>` | `--limit N` | 语义搜索 |
| `serve` | `--transport, --port, --host` | 启动 MCP 服务器 |

---

## 11. 外部依赖与服务

### 11.1 阿里云百炼 DashScope
- 必须配置 `DASHSCOPE_API_KEY`
- 用于：Embedding、Rerank、Vision OCR（图片文字识别）、摘要/实体提取（LLM）、ASR（音频转写）

### 11.2 阿里云 OSS
- 可选，仅在处理 PDF（MinerU）和音频（ASR）时需要
- 作为中转存储（临时上传 → API 处理 → 自动删除）
- 需要：`ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`, `ALIYUN_OSS_ENDPOINT`, `ALIYUN_OSS_BUCKET`

### 11.3 MinerU API
- 可选，用于高质量 PDF 解析（支持表格、公式、图文混排）
- 需要：`MINERU_API_TOKEN`
- 处理流程：上传文件URL → 提交任务（model_version=vlm）→ 轮询状态 → 下载 ZIP → 提取 .md 文件
- 最大超时：3600 秒
- 结果缓存到本地，避免重复调用

---

## 12. 已知设计约束

1. **MinerU 依赖 OSS 中转**：PDF 文件需要先上传到 OSS 才能给 MinerU API 处理，增加了配置复杂度和网络依赖。

2. **语义分块稳定性**：LLM 语义分块依赖模型返回合法 JSON（分割点列表），有时解析失败会降级到简单分块。

3. **sqlite-vec 规模限制**：暴力 KNN 扫描，向量超过数十万条后性能下降。当前单机个人使用场景下可接受。
