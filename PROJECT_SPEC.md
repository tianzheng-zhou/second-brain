# PersonalBrain 项目完整规格文档

> 完整描述系统的所有功能、数据模型、架构和实现细节。
> 更新日期：2026-03-06

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [术语约定](#2-术语约定)
3. [架构概述](#3-架构概述)
4. [数据模型（数据库）](#4-数据模型数据库)
5. [配置系统](#5-配置系统)
6. [核心模块详解](#6-核心模块详解)
7. [MCP 服务器](#7-mcp-服务器)
8. [管理后台](#8-管理后台)
9. [CLI 工具](#9-cli-工具)
10. [已知设计约束与限制](#10-已知设计约束与限制)

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

## 2. 术语约定

| 文档用语 | 含义 |
| ---------- | ------ |
| **File（文件）** | 用户导入的原始文件（PDF、图片、音频、文本等），对应 `files` 表 |
| **Entry（条目/笔记）** | 一条记忆或笔记，可由用户创建或系统自动生成（如摘要），对应 `entries` 表 |
| **Chunk（分块）** | 文件内容被切分后的文本片段，是搜索和 Embedding 的最小单元，对应 `file_chunks` 表 |
| **Processed Text（处理后文本）** | 非文本文件经过转换后的可读版本（PDF→MD、图片→描述、音频→转写），存储在文件系统 |
| **Enrichment（富化）** | 对文件自动生成摘要和标签的过程 |
| **Ingest（入库）** | 文件导入系统的完整流程：复制 → 解析 → 分块 → Embedding → 摘要 |

---

## 3. 架构概述

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
│  search / ingestion / enrichment                      │
│  indexer / reranker                                   │
│  database / llm ← config_manager                     │
└─────────────────────────┬────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────┐
│               Storage Layer                           │
│  brain.db (SQLite + sqlite-vec)                       │
│  File System (STORAGE_PATH/YYYY-MM/)                  │
└──────────────────────────────────────────────────────┘
```

**并发模型：**

- MCP 服务器基于 asyncio，支持并发请求
- SQLite 使用 WAL 模式，允许并发读取；写入操作通过连接序列化
- 异步入库任务通过 asyncio 任务调度（`asyncio.create_task`），不使用多进程

**数据流（文件入库）：**

MCP `ingest_file` 支持异步模式：立即返回 `task_id`，后台执行入库流程，客户端通过 `get_task_status` 查询进度。CLI 和 Admin 为同步模式。

```
MCP ingest_file(async) / CLI ingest / Admin 上传
    → 创建 task 记录（status=pending）
    → ingestion.process_file()
        → calculate_file_id() [SHA256 前16位]
        → 【去重检查】若 file_id 已存在于 files 表 → 直接返回已有记录，跳过后续步骤
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
        → enrich_file() [摘要 + 标签提取 + 保存 entry + 生成 entry Embedding → vec_items]
    → 更新 task 记录（status=completed / failed）
```

**异常处理：**

- extract_text 失败：task 标记为 failed，已复制的文件保留但不写入 files 表（会产生孤儿文件；管理后台"扫描遗漏文件"功能可检测并处理这些文件）
- save_chunks 失败：回滚已写入的 file_chunks/vec_items/fts_chunks（事务保护）
- enrich_file 失败：文件和索引保留，仅标记 enrichment 未完成，不影响搜索可用性
- 任何阶段的 LLM API 调用均使用 tenacity 重试（最多3次，指数退避）

**数据流（混合搜索）：**
```
MCP search / CLI search
    → search_files(query, limit, ...)
        → 向量分支: generate_embedding(query) → vec_items KNN
        → 全文分支: fts_chunks FTS5 MATCH → BM25 评分
        → 融合去重 + 归一化评分
        → 可选 Rerank
        → 返回结果列表（含溯源信息：source_file_id, chunk_index, page_number）
```

---

## 4. 数据模型（数据库）

### brain.db（位于 STORAGE_PATH/brain.db）

使用 SQLite + sqlite-vec 扩展。数据库初始化时创建所有表和虚拟表。

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
status              TEXT              -- active / archived
```

> - 全文内容不存储在数据库中，通过 `processed_text_path` 指向文件系统中的处理后文本。
> - 删除操作为**硬删除**（直接删除行），不使用 "deleted" 软删除状态。`archived` 状态用于用户主动归档，归档文件不参与搜索但保留数据。

#### `vec_items` 虚拟表（sqlite-vec）
统一向量索引，通过 `source_type` + `source_id` 标识来源。

> **实现注意**：sqlite-vec 的 `vec0` 虚拟表对辅助列（非向量列）的支持取决于版本。若不支持文本辅助列，需改用独立的 `vec_metadata` 普通表做映射（rowid 对应）。实现前须验证当前 sqlite-vec 版本的能力。

```sql
-- 理想方案（若 vec0 支持辅助文本列）：
CREATE VIRTUAL TABLE vec_items USING vec0(
    embedding float[2560],
    source_type TEXT,   -- 'chunk' / 'entry'
    source_id   TEXT    -- 对应 file_chunks.id 或 entries.id
)

-- 降级方案（若不支持）：
CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[2560])
CREATE TABLE vec_metadata (
    rowid       INTEGER PRIMARY KEY,  -- 对应 vec_items 的 rowid
    source_type TEXT,
    source_id   TEXT
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
metadata     TEXT              -- JSON，预留扩展字段（当前未使用，不含文件关联）
created_at   TIMESTAMP
source       TEXT              -- mcp / auto_enrichment / cli 等
tags         TEXT              -- JSON 数组字符串
status       TEXT              -- active / archived
```

> - 文件关联只通过 `entry_files` 表维护，不在 entries 内部重复存储。
> - 删除为硬删除，规则同上。

#### `entry_files` 表（多对多关联）
笔记与文件的关联关系。
```sql
entry_id TEXT
file_id  TEXT
PRIMARY KEY (entry_id, file_id)
```

#### `fts_chunks` 虚拟表（FTS5 全文检索）
用于关键词精确匹配，与向量搜索互补。
```sql
CREATE VIRTUAL TABLE fts_chunks USING fts5(
    chunk_id,       -- 对应 file_chunks.id
    content,        -- 分块文本内容（与 file_chunks.content 同步）
    tokenize='unicode61'
)
```

> **中文分词限制**：`unicode61` 按 Unicode 字符边界分词，中文会被逐字切分而非按词语。这意味着中文全文检索退化为逐字匹配，对短查询效果尚可，但无法正确匹配多字词语。当前版本接受此限制，依靠向量搜索弥补。未来可引入 jieba 自定义 tokenizer 改进。

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

### 级联删除规则

**删除文件（`delete_file`）：**

1. 删除 `file_chunks` 中该文件的所有分块
2. 删除 `vec_items` 中 `source_type='chunk'` 且 `source_id` 匹配的向量
3. 删除 `fts_chunks` 中对应的全文索引
4. 删除 `entry_files` 中该文件的所有关联
5. 删除 `entries` 中 `source='auto_enrichment'` 且仅关联此文件的自动生成条目（及其在 vec_items 中的向量）
6. 删除 `files` 表记录
7. 删除文件系统中的原始文件和 processed 文件
8. 以上数据库操作在同一事务中执行

**删除条目（`delete_entry`）：**

1. 删除 `vec_items` 中 `source_type='entry'` 且 `source_id` 匹配的向量
2. 删除 `entry_files` 中该条目的所有关联
3. 删除 `entries` 表记录
4. 以上操作在同一事务中执行

---

## 5. 配置系统

### 双层配置架构

系统有两个配置模块，职责分离：

- **`config.py`**（环境变量层）：仅负责从 `.env` / 环境变量加载凭证和路径，导出为模块级常量（如 `DASHSCOPE_API_KEY`、`STORAGE_PATH`）。不含业务逻辑。
- **`config_manager.py`**（业务配置层）：统一配置入口，提供 `config.get(key)` 接口。优先级：**环境变量 > model_config.json > 代码默认值**。管理模型名、分块参数等可动态修改的业务配置。

### 5.1 环境变量（.env）

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
| `MINERU_USE_SYSTEM_PROXY` | MinerU 请求是否走系统代理（VPN 环境下可能需关闭） | `true` |
| `DELETE_CONFIRMATION` | MCP/CLI 删除操作是否需要确认 | `true` |

### 5.2 业务配置（model_config.json）

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

> - Embedding 维度：2560（qwen3-vl-embedding）
> - 阿里云百炼同样提供了 kimi-k2.5 的 API 服务
> - 若更换 embedding 模型导致维度变化，需重建所有向量索引（可通过全局 refresh_index 实现）

---

## 6. 核心模块详解

### 6.1 database.py — 数据库操作

所有数据库读写的唯一入口，其他模块不直接执行 SQL。

**连接管理：**

- 使用模块级单连接（`sqlite3.connect`），进程生命周期内复用
- 启用 WAL 模式，支持并发读取
- 写入操作通过 SQLite 内部锁序列化，无需应用层写入队列
- 连接配置：`check_same_thread=False`（允许跨线程使用，asyncio 场景需要）

**初始化：**

- `init_db()`：创建所有表和虚拟表（若不存在），加载 sqlite-vec 扩展，启用 WAL 模式
- `reset_db()`：删除并重建数据库（带二次确认）

**文件操作：**

- `save_file(file_info)` → 写入 files 表
- `get_file(file_id)` → 按 ID 查询文件
- `list_files(type?, status?, limit?, offset?)` → 分页列表
- `delete_file(file_id)` → 级联删除（见 Section 4 级联删除规则）
- `update_file_status(file_id, status)` → 更新状态

**分块操作：**

- `save_chunks(chunks, embeddings)` → 批量写入 file_chunks + vec_items + fts_chunks（事务）
- `delete_chunks_for_file(file_id)` → 删除某文件的所有分块及关联的向量和 FTS 索引

**条目操作：**

- `save_entry(entry, embedding?)` → 写入 entries 表；若提供 embedding，同时写入 vec_items（source_type='entry'）
- `get_entry(entry_id)` → 按 ID 查询
- `list_entries(tag?, source?, limit?, offset?)` → 分页列表，支持按 tag/来源过滤
- `update_entry(entry_id, content?, tags?, embedding?)` → 更新内容或标签；若 content 变化且提供新 embedding，更新 vec_items 中对应向量
- `delete_entry(entry_id)` → 级联删除（见 Section 4 级联删除规则）
- `link_entry_file(entry_id, file_id)` → 创建关联

**搜索操作：**

- `vector_search(embedding, limit)` → vec_items KNN 搜索，返回 source_type + source_id + distance
- `fts_search(query, limit)` → fts_chunks FTS5 MATCH 搜索，返回 chunk_id + BM25 分数

**任务操作：**

- `create_task(type, file_path)` → 创建任务记录
- `update_task(task_id, status, result_json?)` → 更新任务状态
- `get_task(task_id)` → 查询任务

### 6.2 models.py — 数据模型

Pydantic 模型，用于模块间数据传递和 MCP 返回值序列化。

- `FileInfo`：文件元数据（对应 files 表字段）
- `FileChunk`：分块内容（id, file_id, chunk_index, content, start_char, page_number）
- `Entry`：条目/笔记（对应 entries 表字段）
- `SearchResult`：搜索结果（score, content, source_type, source_file_id?, source_filename?, chunk_index?, page_number?, entry_id?）——可选字段因 source_type 不同而异，详见 6.5
- `TaskInfo`：任务状态（对应 tasks 表字段）

### 6.3 indexer.py — 文本提取与 Embedding

**文本提取（`extract_text(path, file_type)`）：**

| 文件类型 | 提取方式 | 返回 |
|----------|----------|------|
| PDF | MinerU API（优先）→ 降级到本地 Pillow+Vision OCR | `(markdown_text, image_root_path)` |
| IMAGE | base64 编码 + Vision Model 多模态理解（OCR + 内容描述） | `(text, None)` |
| AUDIO | 上传 OSS → ASR API → 清理 OSS | `(transcript_text, None)` |
| TEXT/MD/CODE | 直接读取文件内容 | `(text, None)` |
| UNKNOWN | 跳过文本提取，记录警告日志，文件仍入库但无法被搜索 | `("", None)` |

> `image_root_path`（仅 PDF）：MinerU 解析 PDF 时提取的图片存放目录。语义分块模式下，Markdown 中的 `![](image_path)` 引用会基于此路径解析为图片 base64，注入 LLM 识别语义边界。简单分块模式下此值不使用。

**PDF 处理降级策略：**
1. 尝试 MinerU API（上传 OSS → 提交任务 → 轮询状态 → 下载 ZIP → 提取 MD 文件）
2. MinerU 失败或不可用 → 本地用 Pillow 将 PDF 每页转为图片 → Vision Model OCR

**MinerU 缓存策略：**
- 缓存目录：`{STORAGE_PATH}/mineru_cache/{file_hash_prefix}/`
- 每次先检查缓存，命中则跳过 API 调用
- 缓存内容：原始 PDF 副本、content_list JSON、Markdown 全文、layout JSON

**分块策略（`generate_embedding_chunks`）：**

1. **简单分块（默认，`use_semantic_split=false`）：**
   - 按字符数分割，`chunk_size=1500`，`chunk_overlap=200`
   - 先按段落（双换行）分，超过 chunk_size 则强制截断
   - 记录每个 chunk 的 `start_char` 偏移量和 `page_number`（PDF 通过页面标记推算）

2. **语义分块（`use_semantic_split=true`）：**
   - 将文本切成段落/图片 token，发给 LLM 识别语义分割点
   - 支持图片嵌入：Markdown 图片语法 `![](path)` 被转换为 base64 注入 LLM
   - 每批最多 30 个段落，超出则滑动窗口处理
   - Chunk 大小范围：目标 chunk_size 的 30%~150%
   - 同样记录 `start_char` 和 `page_number`

**图片内容理解：**
- Vision Model 同时执行 OCR（文字提取）和内容描述（场景、物体、图表含义等）
- 输出合并为一段结构化文本，用于后续 Embedding 和搜索

**处理后文本持久化：**

- PDF：MinerU 解析生成的 Markdown → `{STORAGE_PATH}/processed/{file_id}.md`
- 图片：Vision Model 描述文本 → `{STORAGE_PATH}/processed/{file_id}.md`
- 音频：ASR 转写文本 → `{STORAGE_PATH}/processed/{file_id}.md`
- 文本类文件：无需处理，原文件即可直接阅读
- 路径记录到 `files.processed_text_path`，供 MCP `read_document` 返回

**Embedding 生成：**

- 使用 DashScope embedding 模型（默认 qwen3-vl-embedding，2560 维）
- 支持文本和多模态（含图片的 chunk）
- 多模态消息格式：图片转 base64 + text 混合
- 批量处理，`embedding_batch_size=6`（默认）
- 使用 tenacity 重试（最多3次，指数退避）

**FTS5 索引同步：**
- 每个 chunk 写入 `file_chunks` 时同步写入 `fts_chunks`
- 删除/重建索引时同步清理 FTS5 数据

### 6.4 ingestion.py — 文件入库流程

**目录入库（`process_directory(dir_path)`）：**

- 递归扫描目录下所有文件，过滤条件：
  - 仅包含支持的扩展名（见管理后台支持格式列表）
  - 跳过隐藏文件/目录（以 `.` 开头）
  - 跳过 `__pycache__`、`node_modules` 等常见临时目录
- 逐个调用 `process_file()`，单文件失败不影响其余文件
- 返回汇总结果：成功数、跳过数（去重）、失败数及失败原因

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

### 6.5 search.py — 混合搜索

采用**向量语义搜索 + FTS5 全文检索**的混合策略，兼顾语义理解和精确关键词匹配。

**`search_files(query, limit, use_rerank, time_range, source_type, file_id)`：**

**参数说明：**

- `file_id`（可选）：限定在某个文件的 chunks 范围内搜索，用于 `read_document` 带 query 的场景
- `source_type`（可选）：过滤结果类型，`"chunk"` 只返回文件分块，`"entry"` 只返回笔记
- `time_range`（可选）：时间过滤元组 `(start_datetime, end_datetime)`

**流程：**

1. **向量搜索分支**：
   - 生成查询 Embedding
   - 在 `vec_items` 表执行 KNN（sqlite-vec MATCH）
   - 初始候选量：`max(100, limit * 20)`
   - 通过 `source_type` + `source_id` 直接 JOIN 到 file_chunks / entries
2. **全文检索分支**：
   - 在 `fts_chunks` 表执行 FTS5 MATCH 查询
   - 返回 BM25 评分的候选列表
   - **注意：FTS5 仅索引 file_chunks，不索引 entries**。因此纯关键词匹配无法命中笔记，笔记仅通过向量搜索可达。这是当前版本的设计取舍——entries 通常是摘要文本，向量搜索已足够覆盖。
3. **结果融合**：
   - 合并两路结果，去重（同一 source_id 取较高分）
   - 统一归一化评分
4. 排除 `archived` 状态的文件/条目（归档内容不参与搜索）
5. 按 `source_type` 过滤（chunk / entry）
6. 按时间范围过滤（`time_range` 元组）
7. 可选 Rerank（via DashScope REST API）
8. 返回 `SearchResult` 列表，字段含义因 source_type 不同而异：

| 字段 | chunk 结果 | entry 结果 |
| ------ | ------ | ------ |
| `score` | 归一化评分 | 归一化评分 |
| `content` | chunk 文本片段 | entry 完整内容 |
| `source_type` | `"chunk"` | `"entry"` |
| `source_file_id` | 所属文件 ID | `None`（笔记不一定关联文件） |
| `source_filename` | 所属文件名 | `None` |
| `chunk_index` | chunk 在文件中的序号 | `None` |
| `page_number` | 所在页码（仅 PDF） | `None` |
| `entry_id` | `None` | 对应的 entry ID |

### 6.6 enrichment.py — 自动摘要与标签提取

**`enrich_file(file_obj, text, chunks)`：**

- `file_obj`：文件元数据（FileInfo），用于获取文件名、类型等上下文
- `text`：文件的完整提取文本，用于生成摘要
- `chunks`：文件的分块列表，用于大文件摘要时选取代表性片段

**摘要生成策略（依赖 LLM）：**

- 估算 token 数（CJK: 1.2/字，其他: 0.35/字，图片: 1000/张）
- `token <= 20000`：全文直接发给 LLM 生成摘要
- `token > 20000`：选取代表性 chunk（首/尾 + 均匀间隔共10个），生成摘要

**标签提取（依赖 LLM）：**

- 从摘要文本（不是全文）提取 3-5 个 tags
- 将摘要保存为新 entry（source="auto_enrichment"），通过 `entry_files` 关联源文件
- **为新 entry 生成 Embedding** 并写入 `vec_items`（`source_type='entry'`, `source_id=entry_id`），使笔记可被向量搜索命中

### 6.7 reranker.py — 重排序

调用 DashScope 的 REST API（非 SDK），用于搜索结果精排。

- 单文档最大长度：8000 字符（超出截断）
- 支持 `top_n` 参数
- 失败时返回 score=0 的原始顺序（不崩溃）

### 6.8 llm.py — LLM 调用封装

- 使用 openai SDK，base_url 指向 DashScope 兼容端点
- 供 enrichment（摘要/标签提取）和 indexer（语义分块）使用
- 自动识别"思考模型"（模型名包含 `qwen3` 或 `qwq` 等关键词），默认关闭 `enable_thinking`（通过 `extra_body` 参数传递）
- 模型名从 `config_manager` 动态读取

### 6.9 工具模块（utils/）

| 模块 | 职责 |
| ------ | ------ |
| `aliyun_oss.py` | 阿里云 OSS 文件上传/下载/删除，用于 MinerU 和 ASR 的中转存储 |
| `asr_client.py` | 音频转写客户端，调用 DashScope ASR API（上传 → 轮询 → 取结果） |
| `file_ops.py` | 文件类型识别（扩展名映射）、SHA256 哈希计算、存储目录组织（YYYY-MM 归档） |
| `mineru.py` | MinerU PDF 解析 API 客户端（提交任务 → 轮询 → 下载 ZIP → 提取 MD），含本地缓存 |

---

## 7. MCP 服务器

基于 FastMCP，支持三种传输方式：Stdio（本地客户端）、SSE、Streamable HTTP（远程客户端）。

**MCP 是系统的主要对外接口**，所有记忆存取操作都通过 MCP 工具暴露给客户端。

### 7.1 MCP 工具集

#### 搜索与检索

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `search` | `query: str, limit: int = 5, time_range?: str, source_type?: str` | 混合搜索（向量+全文），返回结果含溯源信息（source_file_id, chunk_index, page_number） |
| `read_note` | `entry_id: str` | 按 ID 读取某条笔记的完整内容 |
| `read_document` | `file_id: str, query?: str` | 读取文件的处理后文本；大文件可带 query 做局部检索（见下方说明） |
| `list_notes` | `tag?: str, source?: str, limit?: int, offset?: int` | 列出笔记，支持按 tag/来源过滤和分页。返回列表及 `total_count`（符合过滤条件的总数，用于分页） |
| `list_files` | `type?: str, status?: str, limit?: int, offset?: int` | 列出文件，支持按类型/状态过滤和分页。返回列表及 `total_count` |
| `get_file_info` | `file_id: str` | 获取文件元信息（大小、类型、创建时间等）及关联的 enrichment 摘要和 tags（通过 `entry_files` JOIN `entries` 获取 `source='auto_enrichment'` 的条目） |

**`read_document` 的 `query` 参数：**

- 不带 query：返回完整的处理后文本
- 带 query：在该文件的 chunks 中进行向量相似度搜索，返回最相关的片段而非全文。适用于大文件（如长 PDF），避免返回过多内容

**`search` 的 `time_range` 参数：**

- 格式：自然语言时间表达，使用 dateparser 解析（如 `"最近一周"`、`"2024年1月到3月"`、`"last 7 days"`）
- 解析后转为 `(start_datetime, end_datetime)` 元组，用于过滤 `created_at` 字段

#### 写入与修改

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `write_note` | `content: str, tags?: list[str], file_paths?: list[str]` | 写入笔记并生成 Embedding（写入 vec_items）；可附带文件路径（同步 ingest 后通过 entry_files 关联） |
| `update_note` | `entry_id: str, content?: str, tags?: list[str]` | 修改笔记内容或 tags；若 content 变化，重新生成 Embedding 并更新 vec_items |
| `delete_note` | `entry_id: str, confirm?: bool` | 删除笔记（级联删除关联向量和 entry_files）。确认行为同 `delete_file` |
| `ingest_file` | `path: str, async?: bool = true` | 导入文件或目录；默认异步，返回 task_id。目录模式递归扫描所有支持格式的文件（见管理后台支持格式列表），忽略隐藏文件和目录 |
| `delete_file` | `file_id: str, confirm?: bool` | 删除文件（级联删除关联的 chunks/embeddings/FTS/entries）。`confirm` 由 `DELETE_CONFIRMATION` 环境变量控制是否需要确认，默认 true |

#### 系统管理

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `get_stats` | 无 | 知识库统计（返回：总文件数、各类型文件数、总笔记数、总 chunk 数、总向量数、存储目录大小、数据库文件大小） |
| `refresh_index` | `file_id?: str` | 重建索引。传 file_id 重建单文件；不传则重建所有文件（异步，返回 task_id） |
| `get_task_status` | `task_id: str` | 查询异步任务状态和结果（入库进度、成功/失败等） |

**`write_note` 的 `file_paths` 行为：**

- 指定的文件路径会同步执行 ingest（非异步），等待所有文件入库完成后返回
- 入库的文件通过 `entry_files` 关联到新创建的笔记
- 若某文件已存在（去重命中），直接关联不重复入库
- 若某文件 ingest 失败，笔记仍会创建，但失败的文件不关联，错误信息在返回值中说明

**MCP 错误处理：**

- 工具调用失败时返回 `is_error=True`，content 包含错误描述
- 常见错误：文件不存在、entry_id 不存在、文件类型不支持、API 调用失败

> MCP 工具是无状态的。对话历史、上下文管理、Agent 循环均由 MCP 客户端负责。

---

## 8. 管理后台

基于 Streamlit，用于系统管理，不涉及对话功能。

**Ingest（导入页）：**
- 从路径导入（文件或文件夹）
- 上传文件导入（支持多文件）
- 支持格式：`png/jpg/jpeg/webp/gif/mp3/wav/ogg/m4a/pdf/txt/md/markdown/json/csv/py/js/html/css/yaml/yml/xml`

**Manage（管理页）：**
- 数据库状态显示
- 初始化数据库按钮
- 重置数据库（删除并重建，带二次确认）
- 扫描遗漏文件：扫描 `STORAGE_PATH` 中存在但未在 `files` 表中记录的文件（包括入库失败产生的孤儿文件），可选择重新入库或删除
- 文件列表（DataFrame 显示）
- 单文件操作：刷新索引 / 删除 / 查看详情 JSON

**Config（配置页）：**
- 查看/修改运行时模型配置
- 数据库 Schema 查看（Debug 用）

---

## 9. CLI 工具

命令：`python -m personal_brain.cli <command>`

| 命令 | 参数 | 功能 |
|------|------|------|
| `init` | 无 | 初始化数据库和目录 |
| `reset` | 无（带二次确认） | 删除并重建数据库 |
| `ingest <path>` | 文件或目录路径 | 批量导入 |
| `search <query>` | `--limit N, --source-type chunk\|entry` | 混合搜索（同 MCP search） |
| `serve` | `--transport, --port, --host` | 启动 MCP 服务器 |

---

## 10. 已知设计约束与限制

1. **MinerU 依赖 OSS 中转**：PDF 文件需要先上传到 OSS 才能给 MinerU API 处理，增加了配置复杂度和网络依赖。

2. **语义分块稳定性**：LLM 语义分块依赖模型返回合法 JSON（分割点列表），有时解析失败会降级到简单分块。

3. **sqlite-vec 规模限制**：暴力 KNN 扫描，向量超过数十万条后性能下降。当前单机个人使用场景下可接受。

4. **FTS5 中文分词**：`unicode61` 分词器不支持中文词语切分，全文检索对中文仅为逐字匹配。当前依靠向量搜索弥补，未来可引入 jieba 等分词方案。

5. **Embedding 模型绑定**：更换不同维度的 embedding 模型需要重建全部向量索引。可通过对所有文件执行 `refresh_index` 实现，但耗时较长且期间搜索结果可能不完整。

6. **SQLite 并发写入**：SQLite 同一时刻仅允许一个写事务。并发写入时 SQLite 通过内部锁自动序列化（配合 busy_timeout），无需应用层写入队列。但多个异步入库任务同时运行时可能出现锁等待。读取不受影响（WAL 模式）。
