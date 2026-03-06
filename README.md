# PersonalBrain

个人知识库记忆后端，基于 MCP 协议，支持多模态文件入库与混合语义检索。

**核心理念：扔进即忘，需时即查。**

---

## 功能概述

- **多模态入库**：文本、Markdown、代码、PDF、图片、音频均可导入，自动提取文本、生成摘要、建立向量索引
- **混合搜索**：向量语义搜索（sqlite-vec KNN）+ FTS5 全文检索，RRF 融合排序，支持可选 Rerank
- **笔记管理**：支持通过 MCP 工具读写笔记（Entry），自动生成摘要条目
- **溯源追踪**：搜索结果可追溯到源文件及文档位置（chunk_index / page_number）
- **MCP 协议**：支持 stdio / SSE / Streamable HTTP 三种传输方式，兼容 Claude Desktop、Cursor 等客户端
- **管理后台**：Streamlit 可视化管理界面，支持文件入库、索引维护、系统配置

---

## 目录结构

```
second-brain/
├── personal_brain/          # 主包
│   ├── config.py            # 环境变量读取
│   ├── config_manager.py    # 业务配置（模型、维度等）
│   ├── models.py            # Pydantic 数据模型
│   ├── database.py          # SQLite + sqlite-vec 数据库操作
│   ├── llm.py               # LLM / Embedding API 封装
│   ├── indexer.py           # 文本提取、分块、Embedding
│   ├── ingestion.py         # 完整入库流水线
│   ├── enrichment.py        # LLM 自动摘要与标签提取
│   ├── search.py            # 混合/语义/全文/笔记搜索
│   ├── reranker.py          # DashScope Rerank API
│   ├── mcp_server.py        # FastMCP 工具注册
│   ├── cli.py               # Click CLI
│   ├── admin.py             # Streamlit 管理后台
│   └── utils/
│       ├── logger.py        # 结构化 JSON 日志
│       ├── metrics.py       # 内存指标统计
│       ├── file_ops.py      # 文件工具（类型检测、SHA256 ID、组织目录）
│       ├── aliyun_oss.py    # 阿里云 OSS 工具
│       ├── asr_client.py    # DashScope ASR 语音转写
│       └── mineru.py        # MinerU PDF 解析 API
├── start.py                 # 一键启动脚本
├── requirements.txt
└── .env.example
```

---

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/macOS

pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写：

```bash
cp .env.example .env
```

必填项：

```ini
DASHSCOPE_API_KEY=your_dashscope_api_key_here
```

可选项：

```ini
PB_STORAGE_PATH=~/personal_brain_data   # 数据存储路径，默认 ~/personal_brain_data
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 阿里云（PDF 解析 / 音频转写需要）
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
ALIYUN_OSS_BUCKET=

# MinerU PDF 解析（可选，不配置则使用本地 OCR 降级方案）
MINERU_API_TOKEN=
```

### 3. 初始化数据库

```bash
python -m personal_brain.cli init
```

### 4. 启动服务

```bash
# stdio 模式（Claude Desktop 等本地客户端）
python start.py

# SSE 模式（远程连接，端口 8765）
python start.py --transport sse

# 同时启动管理后台（端口 8501）
python start.py --admin

# 仅启动管理后台
python start.py --admin-only
```

---

## Claude Desktop 配置

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "personal-brain": {
      "command": "D:\\python_programs\\second-brain\\.venv\\Scripts\\python.exe",
      "args": ["-m", "personal_brain.cli", "serve"]
    }
  }
}
```

---

## MCP 工具列表

| 工具 | 说明 |
|------|------|
| `search` | 混合搜索（向量 + FTS5 RRF），支持时间过滤 |
| `search_semantic` | 纯向量语义搜索 |
| `search_keyword` | 纯全文检索（FTS5） |
| `search_notes` | 仅搜索笔记（entries） |
| `search_in_document` | 在指定文件内搜索 |
| `read_note` | 读取单条笔记 |
| `read_document` | 读取文件处理后的文本内容 |
| `list_notes` | 列出笔记，支持标签过滤 |
| `list_files` | 列出入库文件，支持类型过滤 |
| `get_file_info` | 获取文件详情 |
| `write_note` | 新建笔记 |
| `update_note` | 更新笔记内容 |
| `delete_note` | 删除笔记 |
| `ingest_file` | 导入文件（支持异步模式，返回 task_id） |
| `archive_file` | 归档文件（不参与搜索但保留数据） |
| `restore_file` | 恢复归档文件 |
| `delete_file` | 删除文件及所有索引 |
| `get_stats` | 获取系统统计（文件数、条目数、向量数等） |
| `refresh_index` | 重建单文件或全局索引 |
| `get_task_status` | 查询异步入库任务进度 |
| `health_check` | 服务健康检查 |

---

## CLI 命令

```bash
# 初始化数据库
python -m personal_brain.cli init

# 重置数据库（危险！清空所有数据）
python -m personal_brain.cli reset

# 导入文件或目录
python -m personal_brain.cli ingest /path/to/file.pdf
python -m personal_brain.cli ingest /path/to/folder --recursive

# 搜索
python -m personal_brain.cli search "查询内容" --mode hybrid --limit 5

# 启动 MCP 服务
python -m personal_brain.cli serve
python -m personal_brain.cli serve --transport sse --port 8765
```

---

## 技术栈

| 组件 | 技术选型 |
|------|----------|
| MCP 框架 | `mcp.server.fastmcp.FastMCP` (mcp >= 1.0.0) |
| 向量数据库 | SQLite + `sqlite-vec`（KNN 搜索） |
| 全文检索 | SQLite FTS5（unicode61 分词器） |
| 混合排序 | RRF（Reciprocal Rank Fusion） |
| LLM | DashScope（通义千问，通过 OpenAI SDK 兼容接口） |
| Embedding | DashScope `qwen3-vl-embedding`（2560d，原生 SDK） |
| Rerank | DashScope Rerank REST API |
| PDF 解析 | MinerU API（降级：PyMuPDF + Vision OCR） |
| 音频转写 | DashScope ASR（经由 Aliyun OSS 中转） |
| 图片理解 | DashScope Vision Model（多模态 OCR + 描述） |
| 管理后台 | Streamlit |
| CLI | Click |
| 重试 | tenacity（最多 3 次，指数退避） |
| 日志 | 结构化 JSON 日志（stderr + 滚动文件） |

---

## 数据存储

所有数据存储在 `PB_STORAGE_PATH`（默认 `~/personal_brain_data`）：

```
personal_brain_data/
├── brain.db           # SQLite 主数据库（含向量索引、FTS5）
├── YYYY-MM/           # 原始文件按月份归档
├── processed/         # 处理后的文本版本（PDF→MD、图片→描述等）
├── logs/              # 滚动日志（保留 7 天）
└── model_config.json  # 业务配置（模型、维度等）
```

---

## Embedding 说明

默认使用 `qwen3-vl-embedding`（2560 维），通过 DashScope 原生 SDK 调用：

```python
# 多模态 embedding（qwen3-vl-embedding 等）
from dashscope import MultiModalEmbedding
MultiModalEmbedding.call(model=model, input=[{"text": t}])

# 文本 embedding（text-embedding-v3 等）
from dashscope import TextEmbedding
TextEmbedding.call(model=model, input=texts)
```

> **注意**：`qwen3-vl-embedding` 不支持 OpenAI 兼容端点（`/embeddings`），必须使用原生 DashScope SDK。

可通过管理后台或直接编辑 `model_config.json` 切换模型，切换后需重建索引（`refresh_index` 全局刷新）。

---

## 许可证

MIT
