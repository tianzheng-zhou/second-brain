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
12. [已知问题与设计缺陷](#12-已知问题与设计缺陷)
13. [未实现的功能](#13-未实现的功能)

---

## 1. 项目定位与目标

PersonalBrain（PB）是一个个人知识库**记忆后端**，核心理念是"扔进即忘，需时即查"。

**系统定位：**
- 纯后端服务，通过 MCP 协议对外提供记忆存取能力
- 不包含对话前端，Agent 智能由 MCP 客户端（Claude Desktop、Cursor 等）承担
- 附带 Streamlit 管理后台，用于数据管理和系统配置

**核心能力：**
- **存储**：支持文本、PDF、图片、音频等多模态文件入库，自动提取文本、生成摘要和向量索引
- **检索**：语义搜索，支持时间过滤和类型过滤
- **管理**：笔记的增删改查、文件管理、索引维护

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
│   ├── config.py                # 全局配置（路径、模型、API Key）
│   ├── core/
│   │   ├── cleaner.py           # 垃圾评分算法
│   │   ├── config_manager.py    # 运行时可修改的模型配置管理
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
│  indexer.py / reranker.py / cleaner.py                │
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
```
MCP ingest_file / CLI ingest / Admin 上传
    → ingestion.process_file()
        → calculate_file_id() [SHA256 前16位，去重]
        → organize_file() [复制到 STORAGE_PATH/YYYY-MM/]
        → extract_text() [根据类型选择解析器]
            → PDF: MinerU API (上传OSS → 提交任务 → 轮询 → 下载ZIP → 提取MD)
                   降级: 本地 Pillow 转图片 → vision model OCR
            → IMAGE: base64 → vision model OCR
            → AUDIO: 上传OSS → ASR API 轮询 → 删除OSS临时文件
            → TEXT/MD/CODE: 直接读取
        → calculate_trash_score() [评分规则见下]
        → save_file() [写入 files 表]
        → generate_embedding_chunks() [分块 + 批量 Embedding]
            → semantic_text_splitter() 或 simple_split()
            → 调用 qwen3-vl-embedding API
        → save_chunks() [写入 file_chunks + chunk_embeddings + vec_items]
        → enrich_file() [摘要 + 实体提取 + 保存 entry]
```

**数据流（语义搜索）：**
```
MCP search / CLI search
    → search_files(query, limit, ...)
        → generate_embedding(query)
        → vec_items KNN (sqlite-vec MATCH)
        → rowid 映射到 chunks / entries
        → 可选 Rerank (qwen3-vl-rerank)
        → 返回结果列表
```

---

## 5. 数据模型（数据库）

### brain.db（位于 STORAGE_PATH/brain.db）

#### `files` 表
文件元数据，每个入库文件对应一条记录。
```sql
id          TEXT PRIMARY KEY  -- SHA256[:16]，基于内容哈希，天然去重
path        TEXT UNIQUE       -- 文件在 STORAGE_PATH 的绝对路径
filename    TEXT
type        TEXT              -- image / audio / text / pdf / unknown
size_bytes  INTEGER
created_at  TIMESTAMP
last_accessed TIMESTAMP
ocr_text    TEXT              -- 提取的全文内容（Markdown格式，含图片引用）
trash_score REAL              -- 0.0=垃圾, 1.0=重要
status      TEXT              -- active / archived / deleted
```

#### `vec_items` 虚拟表（sqlite-vec）
向量索引，所有 Embedding 共用一张表（通过 rowid 映射）。
```sql
CREATE VIRTUAL TABLE vec_items USING vec0(
    embedding float[2560]
)
```

#### `file_chunks` 表
文件分块内容。
```sql
id          TEXT PRIMARY KEY  -- "{file_id}_{chunk_index}"
file_id     TEXT
chunk_index INTEGER
content     TEXT
```

#### `chunk_embeddings` 表
分块向量映射。
```sql
rowid    INTEGER PRIMARY KEY  -- 对应 vec_items.rowid
chunk_id TEXT                 -- 对应 file_chunks.id
```

#### `entries` 表
核心记忆/笔记，用户主动写入或 enrichment 自动生成的摘要。
```sql
id              TEXT PRIMARY KEY  -- UUID
content_text    TEXT              -- 主文本内容
content_json    TEXT              -- JSON，含 file_ids、file_paths、description 等
entry_type      TEXT              -- text_only / file_only / mixed
created_at      TIMESTAMP
source          TEXT              -- mcp / auto_enrichment / cli 等
tags            TEXT              -- JSON 数组字符串
importance      REAL              -- 0.0-1.0
trash_score     REAL
status          TEXT              -- active / archived / deleted
```

#### `entry_embeddings` 表
笔记的向量映射（整条 entry 一个 Embedding）。
```sql
rowid    INTEGER PRIMARY KEY
entry_id TEXT
```

#### `entry_files` 表（多对多关联）
笔记与文件的关联关系。
```sql
entry_id TEXT
file_id  TEXT
PRIMARY KEY (entry_id, file_id)
```

---

## 6. 配置系统

### 6.1 静态配置（config.py）

通过环境变量 + .env 文件加载。

| 变量 | 含义 | 默认值 |
|------|------|--------|
| `DASHSCOPE_API_KEY` | 阿里云百炼 API Key | 必填 |
| `DASHSCOPE_BASE_URL` | DashScope 兼容 OpenAI 端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `PB_STORAGE_PATH` | 数据存储目录 | `~/personal_brain_data` |
| `PB_DB_PATH` | 数据库文件路径 | `{STORAGE_PATH}/brain.db` |
| `USE_SEMANTIC_SPLIT` | 是否使用 LLM 语义分块 | `false` |
| `SEMANTIC_SPLIT_MODEL` | 语义分块使用的模型 | `qwen3.5-flash` |
| `CHUNK_SIZE` | 目标分块大小（字符数） | `1500` |
| `CHUNK_OVERLAP` | 分块重叠（字符数） | `200` |
| `ALIYUN_ACCESS_KEY_ID` | 阿里云 AccessKey ID | 可选（PDF/音频处理需要） |
| `ALIYUN_ACCESS_KEY_SECRET` | 阿里云 AccessKey Secret | 可选 |
| `ALIYUN_OSS_ENDPOINT` | OSS 端点 | `oss-cn-hangzhou.aliyuncs.com` |
| `ALIYUN_OSS_BUCKET` | OSS Bucket 名 | 可选 |
| `MINERU_API_TOKEN` | MinerU API Token | 可选（PDF 解析） |
| `MINERU_BASE_URL` | MinerU API 地址 | `https://mineru.net/api/v4` |
| `MINERU_USE_SYSTEM_PROXY` | 是否使用系统代理 | `true` |

### 6.2 运行时配置（config_manager.py）

单例模式，持久化到 `{STORAGE_PATH}/model_config.json`。

**默认值：**
```json
{
  "embedding_model": "qwen3-vl-embedding",
  "rerank_model": "qwen3-vl-rerank",
  "vision_model": "qwen3-vl-plus",
  "enrichment_model": "qwen-plus",
  "embedding_batch_size": 2,
  "use_semantic_split": false,
  "semantic_split_model": "qwen3.5-flash",
  "chunk_size": 1500,
  "chunk_overlap": 200
}
```

---

## 7. 核心模块详解

### 7.1 indexer.py — 文本提取与 Embedding

**文本提取（`extract_text(path, file_type)`）：**

| 文件类型 | 提取方式 | 返回 |
|----------|----------|------|
| PDF | MinerU API（优先）→ 降级到本地 Pillow+Vision OCR | `(markdown_text, image_root_path)` |
| IMAGE | base64 编码 + Vision Model（qwen3-vl-plus）多模态 OCR | `(text, None)` |
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

2. **语义分块（`USE_SEMANTIC_SPLIT=true`）：**
   - 使用 `semantic_text_splitter()`
   - 将文本切成段落/图片 token，发给 LLM 识别语义分割点
   - 支持图片嵌入：Markdown 图片语法 `![](path)` 被转换为 base64 注入 LLM
   - 每批最多 30 个段落，超出则滑动窗口处理
   - Chunk 大小范围：目标 CHUNK_SIZE 的 30%~150%

**Embedding 生成（`generate_embedding`）：**
- 使用 DashScope `qwen3-vl-embedding` 模型
- 支持文本和多模态（含图片的 chunk）
- 多模态消息格式：图片转 base64 + text 混合
- 批量处理，`embedding_batch_size=2`（默认）
- 使用 tenacity 重试（最多3次，指数退避）

### 7.2 ingestion.py — 文件入库流程

**`process_file(file_path)` 完整流程：**
1. 计算文件内容 SHA256[:16] 作为 `file_id`
2. 查询 DB，已存在则直接返回（去重）
3. `organize_file()`：将文件复制到 `STORAGE_PATH/YYYY-MM/filename`（同名不同内容自动重命名）
4. 确定文件类型（扩展名优先）
5. `extract_text()`：提取文本内容
6. `calculate_trash_score()`：评分
7. `save_file()`：写入 files 表
8. 如有文本且 `trash_score > 0.2`：生成 Embedding chunks 并保存
9. `enrich_file()`：自动摘要 + 实体提取

**`refresh_index_for_file(file_id)` 重建索引：**
1. 重新提取文本
2. 更新 DB ocr_text
3. 重新生成 chunks + embeddings
4. 重新运行 `enrich_file()`

### 7.3 cleaner.py — 垃圾评分

`calculate_trash_score(file)` → `float [0.0, 1.0]`（0=垃圾，1=重要）

| 规则 | 分数变化 |
|------|----------|
| 文本内容 < 10 字符 | -0.5 |
| 图片文件 < 50KB | -0.3 |
| 文件名含 "screenshot" | -0.2 |
| 90天未访问 | -0.2 |
| 7天内创建（保护期） | +0.1 |

> 注：文件入库时只有 `trash_score > 0.2` 的文件才会生成 Embedding。

### 7.4 search.py — 语义搜索

**`search_files(query, limit, use_rerank, time_range, entry_type, file_id)`：**

1. 生成查询 Embedding
2. 在 `vec_items` 表执行 KNN（sqlite-vec MATCH）
   - 初始候选量：`max(100, limit * 20)`
3. 将 rowid 映射：
   - `chunk_embeddings` → file chunks
   - `entry_embeddings` → entries（笔记）
4. 按 `entry_type` 过滤（file / text / mixed）
5. 按时间范围过滤（`time_range` 元组）
6. 可选 Rerank（`qwen3-vl-rerank`，via REST API）
7. 返回候选列表，含 `score`（rerank 分 或 `1/(1+distance)`）

### 7.5 enrichment.py — 自动摘要与标签提取

**`enrich_file(file_obj, text, chunks, embeddings)`：**

**摘要生成策略（依赖 LLM）：**
- 估算 token 数（CJK: 1.2/字，其他: 0.35/字，图片: 1000/张）
- `token <= 20000`：全文直接发给 LLM 生成摘要
- `token > 20000`：选取代表性 chunk（首/尾 + 均匀间隔共10个），生成摘要

**标签提取（依赖 LLM）：**
- 从摘要文本（不是全文）提取 3-5 个 tags
- 将摘要保存为新 entry（importance=0.8，source="auto_enrichment"）

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
| `search` | `query: str, limit: int = 5, time_range?: str, entry_type?: str` | 语义搜索知识库，支持时间和类型过滤 |
| `read_note` | `entry_id: str` | 按 ID 读取某条笔记的完整内容 |
| `read_document` | `file_id: str, query?: str` | 读取文件内容；大文件时可带 query 做局部检索 |
| `list_notes` | `tag?: str, source?: str, limit?: int, offset?: int` | 列出笔记，支持按 tag/来源过滤和分页 |
| `list_files` | `type?: str, status?: str, limit?: int, offset?: int` | 列出文件，支持按类型/状态过滤和分页 |
| `get_file_info` | `file_id: str` | 获取文件元信息（大小、类型、创建时间、tags 等，不含全文） |

#### 写入与修改

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `write_note` | `content: str, tags?: list[str], file_paths?: list[str]` | 写入笔记/记忆，可附带文件（触发 ingest） |
| `update_note` | `entry_id: str, content?: str, tags?: list[str]` | 修改笔记内容或 tags，重新生成 Embedding |
| `delete_note` | `entry_id: str` | 删除笔记 |
| `ingest_file` | `path: str` | 导入文件或目录到知识库 |
| `delete_file` | `file_id: str` | 删除文件及其关联的 chunks/embeddings |

#### 系统管理

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `get_stats` | 无 | 知识库统计概览（文件数、笔记数、存储大小等） |
| `refresh_index` | `file_id: str` | 重建某文件的索引（重新提取文本、分块、Embedding） |

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

## 12. 已知问题与设计缺陷

1. **vec_items 共用一张表**：chunk embeddings 和 entry embeddings 共用 `vec_items` 虚拟表，通过 rowid 分别用映射表关联。删除时需要手动维护 rowid 一致性，容易出错。

2. **MinerU 依赖 OSS 中转**：PDF 文件需要先上传到 OSS 才能给 MinerU API 处理，增加了配置复杂度和网络依赖。

3. **Embedding 批次问题**：`embedding_batch_size=2` 默认值很小，批量处理大文件效率低。

4. **config_manager 配置项混乱**：`config.py` 和 `config_manager.py` 都定义了模型名，前者是编译时硬编码，后者是运行时可修改，职责边界不清。

5. **语义分块稳定性**：LLM 语义分块依赖模型返回合法 JSON（分割点列表），有时解析失败会降级。

6. **搜索候选过滤效率**：搜索先取 100+ 候选，然后在 Python 层循环查询 DB 做 rowid 映射，数据量大时 N+1 查询问题明显。

7. **ASR 无代理配置**：ASR 客户端直接调用 DashScope API，不走代理配置。

---

## 13. 未实现的功能

1. **垃圾清理**：`cleanup` CLI 命令逻辑未实现。