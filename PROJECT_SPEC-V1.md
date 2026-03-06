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
11. [可观测性](#11-可观测性)

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

```text
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

MCP `ingest_file` 支持异步模式（`async=true`，默认）：立即返回 `task_id`，后台执行入库流程，客户端通过 `get_task_status` 查询进度。同步模式（`async=false`）和 CLI/Admin 直接返回结果，不创建 task 记录。

```text
[异步] MCP ingest_file(async=true)
    → 创建 task 记录（status=pending），立即返回 {task_id}，后台执行下方流程

[同步] MCP ingest_file(async=false) / CLI ingest / Admin 上传
    → 直接执行下方流程，不创建 task 记录

两种模式均执行：
    → ingestion.process_file()
        → calculate_file_id() [SHA256 前16位]
        → 【去重检查】查询 files 表：
            → file_id 存在且 status='active'   → 直接返回已有记录，跳过后续步骤
            → file_id 存在且 status='archived' → UPDATE status='active'（重激活），直接返回已有记录，跳过后续步骤
            → file_id 不存在                   → 继续入库流程
        → organize_file() [复制到 STORAGE_PATH/YYYY-MM/]
        → extract_text() [根据类型选择解析器]
            → PDF: MinerU API → 提取MD → 保存到 processed/{file_id}.md
                   降级: 本地 Pillow 转图片 → vision model OCR
            → IMAGE: Vision Model 多模态理解（OCR + 内容描述）→ 保存到 processed/{file_id}.md
            → AUDIO: 上传OSS → ASR API 轮询 → 删除OSS临时文件 → 保存到 processed/{file_id}.md
            → TEXT/MD/CODE: 直接读取（无需 processed 副本）
        → [BEGIN TRANSACTION]
            save_file(enrichment_status='pending') [写入 files 表，含 processed_text_path]
            + generate_embedding_chunks() [分块 + 批量 Embedding + 记录位置信息]
            + save_chunks() [写入 file_chunks + vec_items + fts_chunks]
          [COMMIT] / [ROLLBACK on any failure]
        → enrich_file() [摘要 + 标签提取 + 保存 entry + 生成 entry Embedding → vec_items]
            → 成功: UPDATE files SET enrichment_status='completed'
            → 失败: UPDATE files SET enrichment_status='failed'（索引已建立，不影响搜索）

[异步] → 更新 task 记录（status=completed / failed）
```

**异常处理：**

- `extract_text` 失败：已复制的文件保留但不写入 files 表（会产生孤儿文件；管理后台"扫描遗漏文件"可检测并处理）
- `save_file` + `save_chunks` 失败：两步在同一事务中执行，失败整体回滚（files 表无记录，无 chunks/向量）；同一文件可重新入库
- `enrich_file` 失败：文件和索引保留，`enrichment_status` 标记为 `'failed'`，不影响搜索可用性；可通过 `refresh_index` 重试
- 任何阶段的 LLM API 调用均使用 tenacity 重试（最多3次，指数退避）

**数据流（混合搜索）：**

```text
MCP search(混合) / search_semantic(纯向量) / search_keyword(纯全文) / search_notes(纯笔记)
    → search.py 对应函数(query, limit, ...)
        → [混合/语义分支] generate_embedding(query) → vec_items KNN
        → [混合/全文分支] fts_chunks FTS5 MATCH → BM25 评分
        → [混合] RRF 融合（score = Σ 1/(60 + rank_i)），去重
        → 排除 archived 状态，按 source_type / time_range 过滤
        → 可选 Rerank
        → 返回 SearchResult 列表（含溯源信息：source_file_id, chunk_index, page_number）
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
enrichment_status   TEXT              -- pending / completed / failed（init 时写入 'pending'，enrich 完成后更新）
```

> - 全文内容不存储在数据库中，通过 `processed_text_path` 指向文件系统中的处理后文本。
> - 删除操作为**硬删除**（直接删除行），不使用 "deleted" 软删除状态。`archived` 状态用于用户主动归档，归档文件不参与搜索但保留数据。
> - `enrichment_status='failed'` 的文件仍可被搜索（chunks/embeddings 已建立），但缺少摘要和标签，可通过 `refresh_index` 重试。

#### `vec_items` 虚拟表（sqlite-vec）

统一向量索引，通过 `source_type` + `source_id` 标识来源。

**版本检测（`init_db()` 阶段自动执行）：**

sqlite-vec 的 `vec0` 虚拟表对辅助文本列（非向量列）的支持取决于版本，`init_db()` 在初始化时自动检测并选择方案：

```python
# 检测辅助列支持（in init_db()）
try:
    conn.execute("CREATE VIRTUAL TABLE _vec_test USING vec0(x float[1], y TEXT)")
    conn.execute("DROP TABLE _vec_test")
    vec_impl = "aux_column"   # 方案 A：辅助列直接存在虚拟表中
except:
    vec_impl = "metadata_table"  # 方案 B：降级，独立 vec_metadata 表
# 将 vec_impl 写入 model_config.json，后续操作据此分支
```

```sql
-- 方案 A（vec_impl = "aux_column"）：
CREATE VIRTUAL TABLE vec_items USING vec0(
    embedding float[{embedding_dim}],   -- 维度取自 model_config.json 的 embedding_dim
    source_type TEXT,                   -- 'chunk' / 'entry'
    source_id   TEXT                    -- 对应 file_chunks.id 或 entries.id
)

-- 方案 B（vec_impl = "metadata_table"）：
CREATE VIRTUAL TABLE vec_items USING vec0(embedding float[{embedding_dim}])
CREATE TABLE vec_metadata (
    rowid       INTEGER PRIMARY KEY REFERENCES vec_items(rowid),
    source_type TEXT NOT NULL,
    source_id   TEXT NOT NULL
)
CREATE INDEX idx_vec_metadata_source ON vec_metadata(source_type, source_id)
```

> - 两种方案对上层代码透明：`database.py` 的向量读写函数内部按 `vec_impl` 分支处理，其他模块无感知。
> - 搜索时通过 rowid JOIN vec_metadata（方案 B）或直接读辅助列（方案 A），均避免 N+1 查询。

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

仅用于异步 MCP 操作的任务状态追踪（同步模式和 CLI/Admin 不创建任务记录）。

```sql
id          TEXT PRIMARY KEY  -- UUID
type        TEXT              -- ingest / refresh_index
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
| ------ | ------ | ------ |
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
  "embedding_dim": 2560,
  "rerank_model": "qwen3-vl-rerank",
  "vision_model": "kimi-k2.5",
  "enrichment_model": "kimi-k2.5",
  "embedding_batch_size": 6,
  "use_semantic_split": false,
  "semantic_split_model": "qwen3.5-flash",
  "chunk_size": 1500,
  "chunk_overlap": 200,
  "vec_impl": "aux_column"
}
```

> - `embedding_dim`：向量维度，与 `vec_items` 的 `float[{embedding_dim}]` 对应。由 `init_db()` 根据所配置的 embedding 模型写入（默认 2560，对应 qwen3-vl-embedding）。**不应手动修改**；更换不同维度的模型时须先执行全局 `refresh_index` 重建向量索引，`init_db()` 会同步更新此值。
> - `vec_impl` 由 `init_db()` 自动检测写入，**不应手动修改**；若手动改动导致与实际表结构不一致，可通过 `reset_db()` 重建解决
> - 阿里云百炼同样提供了 kimi-k2.5 的 API 服务

---

## 6. 核心模块详解

### 6.1 database.py — 数据库操作

所有数据库读写的唯一入口，其他模块不直接执行 SQL。

**连接管理：**

- 使用模块级单连接（`sqlite3.connect`），进程生命周期内复用
- 启用 WAL 模式（`PRAGMA journal_mode=WAL`），支持并发读取
- 写入操作通过 SQLite 内部锁序列化，无需应用层写入队列
- 连接配置：`check_same_thread=False`（允许跨线程使用，asyncio 场景需要）
- 设置 `busy_timeout=10000`（10秒），并发写入时等待而非立即报错

**初始化：**

- `init_db()`：
  1. 加载 sqlite-vec 扩展
  2. 启用 WAL 模式
  3. **检测 sqlite-vec 辅助列支持**（见 Section 4 vec_items），写入 `vec_impl` 到 model_config.json
  4. 读取 `embedding_model` 配置，确定对应维度，写入 `embedding_dim` 到 model_config.json
  5. 按检测结果创建 `vec_items`（及可能的 `vec_metadata`）和其他所有表/虚拟表
- `reset_db()`：删除并重建数据库（带二次确认）

**文件操作：**

- `save_file(file_info)` → 写入 files 表（`enrichment_status` 初始为 `'pending'`）
- `get_file(file_id)` → 按 ID 查询文件
- `list_files(type?, status?, enrichment_status?, limit?, offset?)` → 分页列表，支持按类型/状态/enrichment_status 过滤
- `delete_file(file_id)` → 级联删除（见 Section 4 级联删除规则）
- `archive_file(file_id)` → 设置 `files.status = 'archived'`（不删除数据，归档文件不参与搜索）
- `restore_file(file_id)` → 设置 `files.status = 'active'`（取消归档，恢复搜索可见性）
- `update_file_enrichment_status(file_id, status)` → 更新 `enrichment_status`（供 ingestion.py 和 refresh_index 调用）

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
| -------- | -------- | ---- |
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
2. **去重检查**：
   - `status='active'`：直接返回已有记录，跳过全部后续步骤
   - `status='archived'`：调用 `restore_file(file_id)` 重激活后返回
   - 不存在：继续
3. `organize_file()`：将文件复制到 `STORAGE_PATH/YYYY-MM/filename`（同名不同内容自动追加后缀重命名，如 `file_1.txt`）
4. 确定文件类型（扩展名优先）
5. `extract_text()`：提取文本内容，保存处理后文本到 `processed/`
6. **BEGIN TRANSACTION** → `save_file(enrichment_status='pending')` + `generate_embedding_chunks()` + `save_chunks()` → **COMMIT**
   [任何失败] → ROLLBACK，files 表无记录，无 chunks/向量；同一文件可重新入库（去重不会命中）
7. `enrich_file()`：自动摘要 + 标签提取
   → 成功：`update_file_enrichment_status(file_id, 'completed')`
   → 失败：`update_file_enrichment_status(file_id, 'failed')`（已建立的搜索索引保留，不影响可搜索性）

**`refresh_index_for_file(file_id)` 重建索引（完整事务保护）：**

> 任何步骤失败均完整回滚，不留中间状态。操作完成前，原索引保持可用。

```text
1. 确认 file_id 存在于 files 表（否则抛 FileNotFoundError）
2. 重新提取文本 → 写入临时路径 {processed_dir}/{file_id}.md.tmp
   [失败] → 删除临时文件，抛错，原数据完整保留
3. BEGIN SAVEPOINT refresh_{file_id}
4.   删除旧 file_chunks（级联删除 vec_items(chunk) + fts_chunks）
5.   删除旧 auto_enrichment entries（仅删除只关联此文件的条目及其 vec_items）
6.   生成新 chunks → 写入 file_chunks + vec_items + fts_chunks
7.   重新运行 enrich_file() → 生成新 entry + embedding → 写入 vec_items
     → 成功: update_file_enrichment_status('completed')
     → 失败: update_file_enrichment_status('failed')（索引已建立，不影响搜索）
   [步骤 4-7 DB 操作失败] → ROLLBACK SAVEPOINT，删除临时文件，抛错，原数据完整保留
8. RELEASE SAVEPOINT（提交）
9. 原子 rename：{file_id}.md.tmp → {file_id}.md
   [失败] → rename 不影响 DB 数据；记录警告日志，下次 refresh 会重建 processed 文件
10. 若 processed_text_path 有变化，UPDATE files 表
```

**`refresh_index_global()` 全局重建（异步，返回 task_id）：**

- 依次对每个 active 文件调用 `refresh_index_for_file()`
- 单文件失败记录错误、继续处理后续文件（不中断整体任务）
- 返回汇总结果：成功数、失败数及失败原因列表

### 6.5 search.py — 混合搜索

提供四种独立搜索函数，MCP 层逐一暴露为 MCP 工具，客户端按场景选择。

**公共参数说明：**

- `limit`：最终返回结果数上限
- `time_range`：时间过滤元组 `(start_datetime, end_datetime)`，过滤 `created_at`
- `use_rerank`：是否启用 DashScope Rerank 精排（仅混合搜索支持）
- `file_id`（仅 `search_in_document`）：限定在某个文件的 chunks 范围内搜索

**四种搜索函数：**

| 函数名 | 向量 | FTS5 | 融合 | 结果类型 |
| ------ | :--: | :--: | :--: | -------- |
| `search_hybrid` | ✓ | ✓ | RRF | chunks + entries |
| `search_semantic` | ✓ | — | — | chunks + entries |
| `search_keyword` | — | ✓ | — | 仅 chunks |
| `search_notes` | ✓ | — | — | 仅 entries |

> **注意：FTS5 仅索引 file_chunks，不索引 entries**。`search_keyword` 不会命中笔记，`search_notes` 不会命中文件分块。

**RRF 融合算法（Reciprocal Rank Fusion，`search_hybrid` 使用）：**

```python
# RRF: score(d) = Σ 1 / (k + rank_i(d))，k=60（标准参数）
# 对每条结果，将其在向量排名和全文排名中的 RRF 分数相加
def rrf_merge(vector_results, fts_results, k=60):
    scores = {}
    for rank, (source_id, _) in enumerate(vector_results):
        scores[source_id] = scores.get(source_id, 0) + 1 / (k + rank + 1)
    for rank, (source_id, _) in enumerate(fts_results):
        scores[source_id] = scores.get(source_id, 0) + 1 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
```

> RRF 无需显式归一化两路分数，对异常分布更鲁棒，是混合搜索融合的推荐方案。

**完整搜索流程（以 `search_hybrid` 为例）：**

1. 生成查询 Embedding → vec_items KNN（候选量：`max(100, limit * 20)`）
2. fts_chunks FTS5 MATCH（候选量同上）
3. RRF 融合，得到候选列表
4. 排除 `archived` 状态的文件/条目
5. 按 `time_range` 过滤
6. 可选 Rerank（via DashScope REST API）
7. 截取 top `limit` 返回

**`search_in_document(file_id, query, limit)`（`read_document` 内部使用）：**

- 纯向量搜索，限定 `file_id` 范围内的 chunks
- 不对外暴露为独立 MCP 工具，由 `read_document` 带 query 时调用

**SearchResult 字段说明（因 source_type 不同而异）：**

| 字段 | chunk 结果 | entry 结果 |
| ---- | --------- | --------- |
| `score` | RRF 或向量距离转换值 | 同左 |
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
- **无降级策略**：API 调用失败时使用 tenacity 重试（最多3次，指数退避），重试耗尽后直接抛出异常，不降级到备用模型。精度和性能优先，由调用方决定如何处理异常。

### 6.9 工具模块（utils/）

| 模块 | 职责 |
| ------ | ------ |
| `aliyun_oss.py` | 阿里云 OSS 文件上传/下载/删除，用于 MinerU 和 ASR 的中转存储 |
| `asr_client.py` | 音频转写客户端，调用 DashScope ASR API（上传 → 轮询 → 取结果） |
| `file_ops.py` | 文件类型识别（扩展名映射）、SHA256 哈希计算、存储目录组织（YYYY-MM 归档） |
| `mineru.py` | MinerU PDF 解析 API 客户端（提交任务 → 轮询 → 下载 ZIP → 提取 MD），含本地缓存 |
| `logger.py` | 结构化日志封装（见 Section 11） |
| `metrics.py` | 性能指标收集（见 Section 11） |

---

## 7. MCP 服务器

基于 FastMCP，支持三种传输方式：Stdio（本地客户端）、SSE、Streamable HTTP（远程客户端）。

**MCP 是系统的主要对外接口**，所有记忆存取操作都通过 MCP 工具暴露给客户端。

### 7.1 MCP 工具集

#### 搜索与检索

| 工具名 | 参数 | 功能 |
| ------ | ---- | ---- |
| `search` | `query: str, limit: int = 5, time_range?: str, use_rerank?: bool` | **混合搜索**（向量 + FTS5，RRF 融合），返回 chunks + entries，含溯源信息 |
| `search_semantic` | `query: str, limit: int = 5, time_range?: str` | **纯向量语义搜索**，命中 chunks 和 entries，适合模糊语义查询 |
| `search_keyword` | `query: str, limit: int = 5, time_range?: str` | **纯全文关键词搜索**（FTS5），仅命中 file chunks，适合精确词语匹配 |
| `search_notes` | `query: str, limit: int = 5, tag?: str` | **笔记专项搜索**（向量搜索，仅 entries），可按 tag 预过滤 |
| `read_note` | `entry_id: str` | 按 ID 读取某条笔记的完整内容 |
| `read_document` | `file_id: str, query?: str` | 读取文件的处理后文本；大文件可带 query 做局部向量检索（见下方说明） |
| `list_notes` | `tag?: str, source?: str, limit?: int, offset?: int` | 列出笔记，支持按 tag/来源过滤和分页。返回列表及 `total_count`（总数，用于分页） |
| `list_files` | `type?: str, status?: str, enrichment_status?: str, limit?: int, offset?: int` | 列出文件，支持按类型/状态/enrichment_status 过滤和分页。返回列表及 `total_count` |
| `get_file_info` | `file_id: str` | 获取文件元信息（大小、类型、创建时间等）及关联的 enrichment 摘要和 tags |

**`time_range` 参数（适用于 `search`、`search_semantic`、`search_keyword`）：**

- 格式：自然语言时间表达，使用 dateparser 解析（如 `"最近一周"`、`"2024年1月到3月"`、`"last 7 days"`）
- 解析后转为 `(start_datetime, end_datetime)` 元组，过滤 `created_at` 字段

**`read_document` 的 `query` 参数：**

- 不带 query：返回完整的处理后文本
- 带 query：在该文件的 chunks 中进行向量相似度搜索，返回最相关的片段（适合长 PDF）

#### 写入与修改

| 工具名 | 参数 | 功能 |
| ------ | ---- | ---- |
| `write_note` | `content: str, tags?: list[str], file_paths?: list[str]` | 写入笔记并生成 Embedding（写入 vec_items）；可附带文件路径（同步 ingest 后通过 entry_files 关联）。返回：`{entry_id, created_at, linked_file_ids, failed_paths}` |
| `update_note` | `entry_id: str, content?: str, tags?: list[str], status?: str` | 修改笔记内容、tags 或状态（`active`/`archived`）；若 content 变化，重新生成 Embedding 并更新 vec_items |
| `delete_note` | `entry_id: str, confirm?: bool` | 删除笔记（级联删除关联向量和 entry_files）。确认行为同 `delete_file` |
| `ingest_file` | `path: str, async?: bool = true` | 导入文件或目录；默认异步，返回 `{task_id}`。目录模式递归扫描所有支持格式的文件，忽略隐藏文件和目录 |
| `archive_file` | `file_id: str` | 归档文件（`status → archived`），不删除数据；归档文件不参与搜索 |
| `restore_file` | `file_id: str` | 取消归档（`status → active`），恢复搜索可见性 |
| `delete_file` | `file_id: str, confirm?: bool` | 删除文件（级联删除关联的 chunks/embeddings/FTS/entries） |

**`delete_file` / `delete_note` 的 confirm 语义：**

两个参数协同控制删除确认行为：

| `DELETE_CONFIRMATION` 环境变量 | `confirm` 参数 | 行为 |
| ----- | ----- | ----- |
| `true`（默认） | 未传或 `false` | 返回确认提示，不执行删除 |
| `true`（默认） | `true` | 直接删除（用户已在参数中确认） |
| `false` | 任意值 | 直接删除，忽略 `confirm` 参数 |

**`write_note` 的 `file_paths` 行为：**

- 指定的文件路径会同步执行 ingest（非异步），等待所有文件入库完成后返回
- 入库的文件通过 `entry_files` 关联到新创建的笔记
- 若某文件已存在（去重命中，含 archived→active 重激活），直接关联不重复入库
- 若某文件 ingest 失败，笔记仍会创建，失败的文件不关联，错误信息在 `failed_paths` 返回

#### 系统管理

| 工具名 | 参数 | 功能 |
| ------ | ---- | ---- |
| `get_stats` | 无 | 知识库统计（返回：总文件数、各类型文件数、总笔记数、总 chunk 数、总向量数、存储目录大小、数据库文件大小） |
| `refresh_index` | `file_id?: str` | 重建索引（完整事务保护，失败全回滚）。传 file_id 重建单文件（同步）；不传则重建所有 active 文件（异步，返回 task_id） |
| `get_task_status` | `task_id: str` | 查询异步任务状态和结果（入库进度、成功/失败等） |
| `health_check` | 无 | 健康检查（见 Section 11），返回系统各组件状态 |

**MCP 错误处理：**

- 工具调用失败时返回 `is_error=True`，content 包含错误描述
- 常见错误：文件不存在、entry_id 不存在、文件类型不支持、API 调用失败
- `refresh_index` 失败时附带 `rollback=true` 字段，说明数据已回滚

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
- 文件列表（DataFrame 显示，含 `enrichment_status` 列）
- 单文件操作：刷新索引 / 归档 / 取消归档 / 删除 / 查看详情 JSON

**Config（配置页）：**

- 查看/修改运行时模型配置
- 数据库 Schema 查看（Debug 用）

---

## 9. CLI 工具

命令：`python -m personal_brain.cli <command>`

| 命令 | 参数 | 功能 |
| ---- | ---- | ---- |
| `init` | 无 | 初始化数据库和目录 |
| `reset` | 无（带二次确认） | 删除并重建数据库 |
| `ingest <path>` | 文件或目录路径 | 批量导入 |
| `search <query>` | `--limit N, --mode hybrid\|semantic\|keyword\|notes` | 搜索（默认 hybrid 混合搜索，同 MCP search 系列工具） |
| `serve` | `--transport, --port, --host` | 启动 MCP 服务器 |

---

## 10. 已知设计约束与限制

1. **MinerU 依赖 OSS 中转**：PDF 文件需要先上传到 OSS 才能给 MinerU API 处理，增加了配置复杂度和网络依赖。

2. **语义分块稳定性**：LLM 语义分块依赖模型返回合法 JSON（分割点列表），有时解析失败会降级到简单分块。

3. **sqlite-vec 规模限制**：暴力 KNN 扫描，向量超过数十万条后性能下降。当前单机个人使用场景下可接受。

4. **FTS5 中文分词**：`unicode61` 分词器不支持中文词语切分，全文检索对中文仅为逐字匹配。当前依靠向量搜索弥补，未来可引入 jieba 等分词方案。

5. **Embedding 模型绑定**：更换不同维度的 embedding 模型需要重建全部向量索引。可通过对所有文件执行 `refresh_index` 实现，但耗时较长且期间搜索结果可能不完整。

6. **SQLite 并发写入**：SQLite 同一时刻仅允许一个写事务。并发写入时 SQLite 通过内部锁自动序列化（配合 busy_timeout），无需应用层写入队列。但多个异步入库任务同时运行时可能出现锁等待。读取不受影响（WAL 模式）。

---

## 11. 可观测性

### 11.1 结构化日志（utils/logger.py）

使用 Python 标准 `logging` 模块，输出为 JSON 格式，便于后续解析和过滤。

**日志级别规范：**

| 级别 | 使用场景 |
| ----- | -------- |
| `DEBUG` | 文本提取进度、chunk 数量、Embedding 生成中间状态 |
| `INFO` | 入库开始/完成、搜索执行（含参数）、任务状态变更 |
| `WARNING` | rename 失败但不影响数据、enrichment 未完成、未知文件类型 |
| `ERROR` | API 调用耗尽重试、数据库操作失败、refresh_index 回滚 |

**日志字段：**

```json
{
  "timestamp": "2026-03-06T12:00:00Z",
  "level": "INFO",
  "module": "ingestion",
  "event": "ingest_completed",
  "file_id": "abc123",
  "filename": "report.pdf",
  "duration_ms": 4200,
  "chunks": 18
}
```

**日志输出目标：**

- 控制台（stderr）：INFO 及以上（开发/调试时可切换到 DEBUG）
- 日志文件：`{STORAGE_PATH}/logs/pb.log`，按日滚动（`TimedRotatingFileHandler`，保留 7 天）

### 11.2 性能指标（utils/metrics.py）

轻量级内存指标收集，使用 `collections.deque` 保存最近 N 条记录（无外部依赖）。

**收集的指标：**

| 指标 | 说明 |
| ---- | ---- |
| `ingest_count` | 累计入库文件数（按结果：success / skip / fail） |
| `ingest_duration_ms` | 最近 100 次入库耗时（用于计算 P50/P95） |
| `search_count` | 累计搜索次数（按类型：hybrid / semantic / keyword / notes） |
| `search_duration_ms` | 最近 100 次搜索耗时 |
| `api_call_count` | LLM/Embedding/Rerank API 调用次数（按结果：success / retry / fail） |
| `vec_items_total` | 向量索引条目总数（启动时读取，每次写入后更新） |

**访问方式：**

- `metrics.get_summary()` 返回当前指标快照（JSON），供 `get_stats` 和 `health_check` 调用

### 11.3 健康检查（MCP `health_check` 工具）

`health_check` 工具返回各组件状态，帮助客户端快速诊断系统是否正常。

**返回值结构：**

```json
{
  "status": "ok",
  "components": {
    "database": { "status": "ok", "size_mb": 45.2 },
    "vec_index": { "status": "ok", "vec_impl": "aux_column", "count": 3821 },
    "storage": { "status": "ok", "path": "/data/pb", "free_gb": 120.5 },
    "dashscope_api": { "status": "ok", "last_success": "2026-03-06T11:58:00Z" }
  },
  "metrics": {
    "ingest_success_rate": 0.98,
    "search_p95_ms": 320,
    "api_fail_rate": 0.01
  }
}
```

**组件状态规则：**

- `"ok"`：正常
- `"degraded"`：可用但有问题（如磁盘剩余 < 5GB、API 失败率 > 5%）
- `"error"`：不可用（如数据库无法打开、存储路径不存在）
- 顶层 `status` 取所有组件中最差的状态

**SSE 模式额外支持：**

SSE 传输模式下额外暴露 HTTP 端点 `GET /health`，返回与 `health_check` 工具相同的 JSON，供外部监控系统（如 UptimeRobot）轮询使用。
