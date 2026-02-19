# PersonalBrain (PB)

**个人信息的"智能垃圾桶"**——无筛选、无结构、多模态涌入，系统自动处理、检索、清理。

## 1. 项目定位

PersonalBrain 是一个旨在处理海量个人信息的系统。它允许用户随意丢入文本、图片、音频等文件，系统会自动进行去重、语义索引、垃圾识别和归档，从而实现"扔进即忘，需时即查"的体验。

## 2. 核心功能

* **多模态摄入**: 支持文本、图片、音频等多种格式文件的无缝录入。
* **语义搜索**: 基于自然语言的跨模态搜索，利用向量数据库进行近似最近邻检索。
* **自动去重**: 通过内容哈希识别并剔除重复文件。
* **垃圾自动识别**: 自动评分，对低质量内容进行归档或删除。
* **本地化与隐私**: 所有数据本地存储，无需联网即可使用核心功能。

## 3. 快速开始 (Quick Start)

### 3.1 前置要求

PersonalBrain 使用阿里云百炼 (DashScope) 提供的云端 AI 模型服务。

1.  注册阿里云账号并开通百炼服务。
2.  获取 API Key。
3.  设置环境变量 `DASHSCOPE_API_KEY`：
    *   Windows (PowerShell): `$env:DASHSCOPE_API_KEY="your-api-key"`
    *   Linux/macOS: `export DASHSCOPE_API_KEY="your-api-key"`

### 3.2 安装依赖

建议使用 Python 3.10+ 环境。

```bash
pip install -r requirements.txt
```

### 3.3 初始化

初始化数据库和存储目录：

```bash
python -m personal_brain.cli init
```

**注意**：如果您之前使用过本地模型版本，请先重置数据库以清除不兼容的向量数据：

```bash
python -m personal_brain.cli reset
```
这将会在用户目录下创建 `personal_brain_data` 文件夹用于存储数据。

### 3.4 导入文件 (Ingest)

将文件或文件夹导入系统（会自动去重、建立索引）：

```bash
# 导入单个文件
python -m personal_brain.cli ingest "path/to/your/file.txt"

# 导入整个文件夹
python -m personal_brain.cli ingest "path/to/your/folder"
```

### 3.5 语义搜索 (Search)

使用自然语言搜索已导入的内容：

```bash
python -m personal_brain.cli search "关于人工智能的笔记"
```

### 3.6 垃圾清理 (Cleanup)

查看并清理低质量文件（如无文本的截图、重复文件等）：

```bash
# 预览清理结果（不实际删除）
python -m personal_brain.cli cleanup --dry-run

# 执行清理（功能开发中）
python -m personal_brain.cli cleanup
```

## 4. 架构设计

系统分为三层：
1.  **Raw Storage (原始存储)**: 原始文件副本，按日期归档。
2.  **Semantic Index (语义索引)**: SQLite + sqlite-vec，存储文本提取、向量嵌入和元数据。
3.  **Knowledge Graph (知识图谱)**: 实体关系网络，用于精确推理和关联分析。

## 5. 命令行接口

```bash
pb init                    # 初始化数据库和目录
pb reset                   # 重置数据库 (切换模型时使用)
pb ingest [path]           # 处理inbox或指定文件
pb search "query"          # 语义搜索
pb cleanup [--dry-run]     # 运行垃圾清理
```

## 6. 图形化界面 (GUI)

本项目提供了一个基于 Streamlit 的智能对话界面，让您可以像与智能体对话一样查询您的知识库。

### 启动 GUI

在项目根目录下运行以下命令：

```bash
streamlit run streamlit_app.py
```

或者直接双击运行 `run_gui.bat` (Windows)。

功能：
- **Chat**: 与 AI 助手对话，基于您的笔记回答问题，并提供来源引用。
- **Ingest**: 导入文件和文件夹。
- **Manage**: 数据库管理。

## 7. Model Context Protocol (MCP) 服务器

PersonalBrain 支持 MCP 协议，可以作为工具被 Claude Desktop、Cherry Studio、Trae 或其他支持 MCP 的客户端集成。

### 功能
- `search_notes`: 搜索笔记。
- `ask_brain_agent`: 基于 RAG 回答问题。
- `ingest_content`: 导入新内容。

### 连接方式

PersonalBrain MCP 服务器支持两种连接方式：

1.  **SSE (Server-Sent Events) 模式**：通过 HTTP 流式传输，兼容性更好，支持远程连接 (推荐 Cherry Studio 使用)。
2.  **Stdio (Standard Input/Output) 模式**：通过标准输入输出通信，适合本地集成 (Claude Desktop 默认方式)。

---

### 方式一：SSE 模式 (推荐 / Cherry Studio)

#### 1. 启动服务器

运行项目根目录下的 `run_mcp_sse.bat`，或者在终端执行：

```bash
python mcp_server.py --transport sse --host 0.0.0.0 --port 8000
```

服务器启动后，SSE 端点地址为：`http://localhost:8000/sse`

#### 2. 配置客户端 (以 Cherry Studio 为例)

1.  打开 Cherry Studio 设置 -> 助手/工具 -> MCP 服务器。
2.  添加新服务器：
    *   **类型**: `SSE`
    *   **URL**: `http://localhost:8000/sse`
    *   **名称**: `personal-brain` (任意)

> **注意**: 请确保在启动服务器的终端或 `.env` 文件中配置了 `DASHSCOPE_API_KEY` 环境变量，否则工具调用会失败。

---

### 方式二：Stdio 模式 (Claude Desktop)

直接配置客户端启动 Python 脚本。

在 Claude Desktop 的配置文件 (`claude_desktop_config.json`) 中添加：

```json
{
  "mcpServers": {
    "personal-brain": {
      "command": "D:/python_programs/second-brain/.venv/Scripts/python.exe",
      "args": [
        "D:/python_programs/second-brain/mcp_server.py",
        "--transport", "stdio"
      ],
      "env": {
        "DASHSCOPE_API_KEY": "your-api-key"
      }
    }
  }
}
```

或者直接运行 `run_mcp.bat` 进行测试 (默认为 stdio 模式)。

