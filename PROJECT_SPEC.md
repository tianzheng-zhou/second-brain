# PersonalBrain 项目完整规格文档

> 本文档用于重构参考，完整描述现有系统的所有功能、数据模型、架构和实现细节。
> 生成日期：2026-03-06

---

## 目录

1. [项目定位与目标](#1-项目定位与目标)
2. [技术栈](#2-技术栈)
3. [目录结构](#3-目录结构)
4. [架构概述](#4-架构概述)
5. [数据模型（数据库）](#5-数据模型数据库)
6. [配置系统](#6-配置系统)
7. [核心模块详解](#7-核心模块详解)
8. [应用层（界面）](#8-应用层界面)
9. [MCP 服务器](#9-mcp-服务器)
10. [CLI 工具](#10-cli-工具)
11. [外部依赖与服务](#11-外部依赖与服务)
12. [已知问题与设计缺陷](#12-已知问题与设计缺陷)
13. [未实现的功能](#13-未实现的功能)

---

## 1. 项目定位与目标

PersonalBrain（PB）是一个个人知识库系统，核心理念是"扔进即忘，需时即查"。

**核心交互范式：**
- **对话即界面（Conversation as Interface）**：用户的所有操作（写入、搜索、管理）均通过聊天窗口完成。
- **读写一体（Read-Write Integration）**：写入与检索在同一上下文中进行，系统根据对话内容自动判断意图。
- **多模态支持**：支持文本、PDF、图片、音频等混合输入。

---

## 2. 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| AI 对话界面 | Chainlit |
| 管理后台 | Streamlit |
| 数据库 | SQLite + sqlite-vec（向量索引扩展） |
| LLM/Embedding | 阿里云百炼 DashScope（兼容 OpenAI 接口） |
| LLM 客户端 | openai SDK |
| MCP 服务 | mcp (FastMCP) |
| PDF 解析 | MinerU API（云端，复杂 PDF）/ 本地降级方案 |
| 音频转写 | DashScope ASR（qwen3-asr-flash-filetrans） |
| 文件云存储 | 阿里云 OSS（用于 MinerU/ASR 中转） |
| 环境变量 | python-dotenv |
| 时间解析 | dateparser |
| 重试 | tenacity |

**主要 Python 包：**
```
click, openai, pydantic, sqlite-vec, pillow, python-magic-bin/python-magic,
requests, tqdm, tenacity, streamlit, mcp, python-dotenv, dashscope,
uvicorn, oss2, dateparser
```

**默认模型配置（可通过 config_manager 动态修改）：**
```
chat_model:      qwen-plus
embedding_model: qwen3-vl-embedding (2560维)
rerank_model:    qwen3-vl-rerank
vision_model:    qwen3-vl-plus
semantic_split_model: qwen3.5-flash
```

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
│   │   ├── ask.py               # Agent 入口（RAG 对话循环）
│   │   ├── chainlit_datalayer.py # Chainlit 自定义 SQLite 数据层
│   │   ├── cleaner.py           # 垃圾评分算法
│   │   ├── config_manager.py    # 运行时可修改的模型配置管理
│   │   ├── database.py          # 所有数据库操作函数
│   │   ├── enrichment.py        # 文件自动摘要/实体提取
│   │   ├── indexer.py           # 文本提取、分块、Embedding 生成
│   │   ├── ingestion.py         # 文件入库主流程
│   │   ├── llm.py               # LLM 调用封装
│   │   ├── models.py            # Pydantic 数据模型
│   │   ├── reranker.py          # 重排序（DashScope REST API）
│   │   ├── search.py            # 语义搜索主函数
│   │   └── tools.py             # Agent 工具函数 + OpenAI 工具定义
│   └── utils/
│       ├── aliyun_oss.py        # 阿里云 OSS 文件操作
│       ├── asr_client.py        # 音频转写客户端
│       ├── file_ops.py          # 文件类型识别、hash、存储组织
│       └── mineru.py            # MinerU PDF 解析 API 客户端
├── chainlit_app.py              # Chainlit 对话界面入口
├── streamlit_app.py             # Streamlit 管理后台（简版）
├── admin_dashboard.py           # Streamlit 管理后台（完整版，待确认）
├── mcp_server.py                # MCP 服务器（stdio/SSE）
├── start_app.py                 # 启动 Chainlit 对话界面
├── start_admin.py               # 启动 Streamlit 管理后台
├── start_all.py                 # 同时启动所有服务
├── run_*.bat                    # Windows 快捷启动脚本
├── .env / .env.example          # 环境变量
├── requirements.txt             # 依赖列表
├── chainlit.db                  # Chainlit 对话历史 SQLite（项目根目录）
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
┌─────────────────────────────────────────────────┐
│               Application Layer                  │
│  ┌─────────────┐ ┌──────────────┐ ┌──────────┐  │
│  │  Chainlit   │ │  Streamlit   │ │   MCP    │  │
│  │  (Chat UI)  │ │  (Admin UI)  │ │  Server  │  │
│  └──────┬──────┘ └──────┬───────┘ └────┬─────┘  │
└─────────┼───────────────┼──────────────┼─────────┘
          │               │              │
┌─────────▼───────────────▼──────────────▼─────────┐
│               Core Business Logic                 │
│  ask.py → tools.py → search.py / ingestion.py    │
│  indexer.py → enrichment.py → reranker.py        │
│  llm.py ← config_manager.py                      │
└─────────────────────────┬───────────────────────-─┘
                          │
┌─────────────────────────▼────────────────────────┐
│               Storage Layer                       │
│  brain.db (SQLite + sqlite-vec)                   │
│  File System (STORAGE_PATH/YYYY-MM/)              │
│  chainlit.db (Chainlit 对话历史)                  │
└──────────────────────────────────────────────────┘
```

**数据流（文件入库）：**
```
用户上传文件
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

**数据流（RAG 对话）：**
```
用户消息
    → ask_brain() [最多5轮 Agent 循环]
        → call_llm(messages, tools=TOOL_DEFINITIONS)
        → 如有工具调用:
            → search_semantic() → search_files() → vec_items KNN + rerank
            → write_entry() → ingest_path() + save_entry() + generate_embedding
            → search_graph() → get_entities_by_name() + get_entity_relations()
            → read_document() → get ocr_text [>20k tokens 则降级到 KNN preview]
            → update_entry() / delete_entry()
        → log_agent_action() [审计日志]
        → 最终答案 streaming 返回 Chainlit
```

---

## 5. 数据模型（数据库）

### 5.1 主数据库 brain.db（位于 STORAGE_PATH/brain.db）

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

#### `file_embeddings` 表（已废弃，保留兼容）
旧版整文件 Embedding 映射。
```sql
rowid   INTEGER PRIMARY KEY
file_id TEXT
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
source          TEXT              -- web_chat / auto_enrichment / cli 等
tags            TEXT              -- JSON 数组字符串
importance      REAL              -- 0.0-1.0
trash_score     REAL
status          TEXT              -- active / archived / deleted
conversation_id TEXT              -- 关联对话会话
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

#### `entities` 表（知识图谱）
从文本中提取的实体。
```sql
id            TEXT PRIMARY KEY  -- UUID
name          TEXT
type          TEXT              -- person / project / location / tech / organization / concept
first_seen    TIMESTAMP
mention_count INTEGER
metadata      TEXT              -- JSON
```

#### `relations` 表（知识图谱）
实体间关系。
```sql
source      TEXT   -- entity.id
target      TEXT   -- entity.id
type        TEXT   -- 关系类型（由 LLM 自由命名）
file_id     TEXT   -- 来源文件
confidence  REAL
created_at  TIMESTAMP
```

#### `conversations` 表
对话会话元数据。
```sql
id         TEXT PRIMARY KEY  -- Chainlit session.id
title      TEXT
created_at TIMESTAMP
updated_at TIMESTAMP
summary    TEXT
```

#### `agent_audit_logs` 表
Agent 操作审计日志。
```sql
id              INTEGER PRIMARY KEY AUTOINCREMENT
conversation_id TEXT
user_query      TEXT
tool_calls      TEXT  -- JSON 数组
tool_results    TEXT  -- JSON 数组
timestamp       TIMESTAMP
```

#### `chat_history` 表（旧版，基本废弃）
全局聊天历史，已被 Chainlit 的 chainlit.db 取代。
```sql
id        INTEGER PRIMARY KEY AUTOINCREMENT
role      TEXT
content   TEXT
timestamp TIMESTAMP
```

### 5.2 Chainlit 数据库 chainlit.db（项目根目录）

Chainlit 对话历史持久化，由 `SQLiteDataLayer` 自定义实现（非 Chainlit 默认）。

包含：`users`, `threads`, `steps`, `elements`, `feedbacks` 表。

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
| `DELETE_CONFIRMATION` | 删除前是否需要确认 | `true` |

**模型常量（config.py 中硬编码，config_manager 可覆盖）：**
```python
EMBEDDING_MODEL = "qwen3-vl-embedding"
EMBEDDING_DIMENSION = 2560
RERANK_MODEL = "qwen3-vl-rerank"
VISION_MODEL = "qwen3-vl-plus"
CHAT_MODEL = "qwen3-max"
```

### 6.2 运行时配置（config_manager.py）

单例模式，持久化到 `{STORAGE_PATH}/model_config.json`。

**默认值：**
```json
{
  "chat_model": "qwen-plus",
  "ai_search_model": "qwen-plus",
  "vision_model": "qwen3-vl-plus",
  "embedding_model": "qwen3-vl-embedding",
  "rerank_model": "qwen3-vl-rerank",
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
4. 删除旧知识图谱数据（relations + 孤立 entities）
5. 重新运行 `enrich_file()`

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
3. 将 rowid 逐一映射（优先级）：
   - `chunk_embeddings` → file chunks（新架构）
   - `file_embeddings` → 整文件（旧架构，兼容）
   - `entry_embeddings` → entries（笔记）
4. 按 `entry_type` 过滤（file / text / mixed）
5. 按时间范围过滤（`time_range` 元组）
6. 可选 Rerank（`qwen3-vl-rerank`，via REST API）
7. 返回候选列表，含 `score`（rerank 分 或 `1/(1+distance)`）

### 7.5 enrichment.py — 自动摘要与知识图谱

**`enrich_file(file_obj, text, chunks, embeddings)`：**

**摘要生成策略：**
- 估算 token 数（CJK: 1.2/字，其他: 0.35/字，图片: 1000/张）
- `token <= 20000`：全文直接发给 LLM 生成摘要
- `token > 20000`：选取代表性 chunk（首/尾 + 均匀间隔共10个），生成摘要

**实体/标签提取：**
- 从摘要文本（不是全文）提取 3-5 个 tags
- 提取 person / org / tech / location / concept 类实体
- 写入 entities 表，重复实体自动累加 mention_count
- 将摘要保存为新 entry（importance=0.8，source="auto_enrichment"）

### 7.6 ask.py — Agent 对话循环

**`ask_brain(query, history, stream, conversation_id, force_retrieve)`：**

- 最多 5 轮 tool call 循环（MAX_TURNS = 5）
- `force_retrieve=True`：在系统提示中强制要求 LLM 先调用 `search_semantic`
- 每轮：`call_llm(messages, tools)` → 处理 tool calls → 追加到 messages
- 自动将 `conversation_id` 注入 `write_entry` 工具调用
- 收集 `search_semantic` 的来源，返回给前端显示 References
- 记录审计日志到 `agent_audit_logs`

**系统提示（Agent 角色）：**
```
You are PersonalBrain.
- Memory: write_entry（保存笔记、关联文件）
- Retrieval: search_semantic（向量搜索）/ search_graph（图谱搜索）
- Maintenance: update_entry（修改笔记）
- 搜索时提取核心关键词而非完整问句
- 时间范围转换为 ISO8601
- 回答引用来源
- 遇到 confirmation_needed 必须向用户确认
```

### 7.7 tools.py — Agent 工具集

| 工具名 | 功能 |
|--------|------|
| `write_entry` | 写入笔记，可附带文件路径（触发 ingest），可选写入知识图谱 |
| `update_entry` | 修改笔记内容或 tags，重新生成 Embedding |
| `search_semantic` | 语义搜索，支持时间过滤和类型过滤 |
| `search_graph` | 查询实体关系图谱 |
| `extract_entities` | 从文本中提取实体和关系 |
| `delete_entry` | 删除笔记（默认需要二次确认，`confirmed=False`） |
| `read_document` | 读取文件全文；>20k tokens 时自动降级为 KNN 预览摘要 |

**`read_document` 大文件处理：**
- 估算 token（同 enrichment.py 算法）
- 超过 20000 token → 执行语义搜索（query="summary abstract introduction conclusion main points"）
- 返回 top 5 chunk 预览 + 建议用户用 search_semantic 精确查询

### 7.8 reranker.py — 重排序

调用 DashScope 的 REST API（非 SDK），模型 `qwen3-vl-rerank`。

- 单文档最大长度：8000 字符（超出截断）
- 支持 `top_n` 参数
- 失败时返回 score=0 的原始顺序（不崩溃）

### 7.9 llm.py — LLM 调用

- 使用 openai SDK，base_url 指向 DashScope 兼容端点
- 支持 `stream=True/False`
- 自动识别 `qwen3.5-flash` 系列"思考模型"，默认关闭 `enable_thinking`
- 模型从 `config_manager.get("chat_model")` 动态读取

---

## 8. 应用层（界面）

### 8.1 Chainlit 对话界面（chainlit_app.py）

**认证：**
- 用户名/密码认证：`admin / admin`（硬编码）
- 用户信息持久化到 `chainlit.db`（通过 SQLiteDataLayer）

**会话管理：**
- 每次对话开始保存 conversation 到 brain.db
- 历史记录保存到 chainlit.db（threads/steps 表）
- 恢复对话时重建 LLM history（取最后 20 条消息）

**消息处理（`@cl.on_message`）：**

1. **命令处理：**
   - `/files`：列出所有文件（内联显示 + delete 按钮）
   - `/side`：列出所有文件（侧边栏显示）

2. **知识库查询意图检测：**
   - 关键词匹配（"知识库"、"存了啥"、"有哪些文件"等）
   - 触发后增强查询，强制 LLM 先搜索数据库

3. **文件上传处理：**
   - 文件保存到 `temp/personal_brain_uploads/` 临时目录
   - 文件路径通过系统上下文注入到 Agent 消息

4. **正常对话（RAG）：**
   - 调用 `ask_brain()`，streaming 返回
   - References 显示在可折叠的 `cl.Step` 中
   - 每条 Reference 是可点击链接 `/ref/{ref_type}/{ref_id}`

**自定义路由：**
- `GET /ref/{ref_type}/{ref_id}`：查看引用内容（file/chunk/entry）
- 返回 HTML 页面，含内容预览 + "导出到文件"按钮
- `ref_type`: `file` | `chunk` | `entry`

**Action 回调：**
- `delete_file`：删除文件记录
- `open_ref`：在侧边栏打开引用内容（最多显示 12000 字符）

### 8.2 Streamlit 管理后台（streamlit_app.py）

三个导航页面：

**Chat（聊天页）：**
- 基本聊天界面（非 streaming 优化版）
- 显示来源 References

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
- 数据库 Schema 查看（Debug 用）

---

## 9. MCP 服务器

文件：`mcp_server.py`，基于 `FastMCP`。

**启动方式：**
- Stdio 模式：`python mcp_server.py --transport stdio`
- SSE 模式：`python mcp_server.py --transport sse --host 0.0.0.0 --port 8000`
  - SSE 端点：`http://localhost:8000/sse`

**提供的工具：**

| 工具名 | 参数 | 功能 |
|--------|------|------|
| `search_notes` | `query: str, limit: int = 5` | 语义搜索知识库 |
| `ask_brain_agent` | `question: str` | RAG 问答（无状态，无历史） |
| `ingest_content` | `path: str` | 导入文件或目录 |

> 注：MCP 工具是无状态的，不保存对话历史。

---

## 10. CLI 工具

命令：`python -m personal_brain.cli <command>`

| 命令 | 参数 | 功能 |
|------|------|------|
| `init` | 无 | 初始化数据库和目录 |
| `reset` | 无（带二次确认） | 删除并重建数据库 |
| `ingest <path>` | 文件或目录路径 | 批量导入 |
| `search <query>` | `--limit N` | 语义搜索 |
| `cleanup` | `--dry-run` | 清理垃圾文件（**未实现**） |

---

## 11. 外部依赖与服务

### 11.1 阿里云百炼 DashScope
- 必须配置 `DASHSCOPE_API_KEY`
- 用于：LLM 对话、Embedding、Rerank、Vision OCR（图片文字识别）、ASR（音频转写）

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

1. **认证写死**：Chainlit 的用户名密码 `admin/admin` 硬编码在 chainlit_app.py 中。

2. **vec_items 共用一张表**：file embeddings、chunk embeddings、entry embeddings 全部共用 `vec_items` 虚拟表，通过 rowid 分别用三张映射表关联。删除时需要手动维护 rowid 一致性，容易出错。

3. **旧版 file_embeddings 残留**：整文件级 Embedding 已被 chunk 级取代，但 `file_embeddings` 表和相关代码仍保留（兼容逻辑）。

4. **数据库在两个位置**：`brain.db`（核心数据）在 `STORAGE_PATH`，`chainlit.db`（对话历史）在项目根目录。路径不统一。

5. **MinerU 依赖 OSS 中转**：PDF 文件需要先上传到 OSS 才能给 MinerU API 处理，增加了配置复杂度和网络依赖。

6. **Embedding 批次问题**：`embedding_batch_size=2` 默认值很小，批量处理大文件效率低。

7. **临时文件不清理**：Chainlit 上传的文件保存在 `temp/personal_brain_uploads/` 后不主动清理。

8. **config_manager 配置项混乱**：`config.py` 和 `config_manager.py` 都定义了模型名，前者是编译时硬编码，后者是运行时可修改，但 `config.py` 的值实际上被 `config_manager` 覆盖，文档和代码不一致。

9. **chat_history 表废弃**：旧版全局聊天历史表未清理，与 Chainlit 的 chainlit.db 重复。

10. **cleanup 命令未实现**：CLI 的 `cleanup` 命令只打印提示，没有实际逻辑。

11. **语义分块稳定性**：LLM 语义分块依赖模型返回合法 JSON（分割点列表），有时解析失败会降级。

12. **搜索候选过滤效率**：搜索先取 100+ 候选，然后在 Python 层循环查询 DB 做 rowid 映射，数据量大时 N+1 查询问题明显。

13. **ASR 无代理配置**：ASR 客户端直接调用 DashScope API，不走 `MINERU_USE_SYSTEM_PROXY` 配置。

---

## 13. 未实现的功能

根据 PRD（`.trae/rules/requirements.md`）规划但尚未实现的功能：

1. **知识图谱完整性**：实体提取时只保存实体到 entities 表，但 entity-file 关系并未通过 relations 表正确关联（`enrich_file` 中有 `pass` 注释）。

2. **垃圾清理**：`cleanup` CLI 命令骨架已有，逻辑未实现。

3. **对话标题自动生成**：`save_conversation()` 时标题固定为 "New Chat"，TODO 注释标记。

4. **Web 搜索集成**：Agent 联网搜索（Future Enhancement）。

5. **主动整理 Agent**：后台定期整理碎片化笔记（Future Enhancement）。

6. **多用户支持**：当前单用户，DB 预留了 `conversation_id` 字段，但没有用户隔离逻辑。

7. **管理后台知识图谱可视化**：entities/relations 数据已存储，但没有可视化界面。
