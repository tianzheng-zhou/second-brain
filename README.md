# PersonalBrain (PB)

> **⚠️ 警告 / Warning**
> 
> 本项目目前处于 **早期开发阶段 (Alpha)**。
> *   **数据库兼容性**: 数据库结构可能会随着版本更新而发生变化，导致旧数据无法读取。请勿在生产环境中使用，并随时做好数据备份。
> *   **API Key 安全**: 请确保您的 API Key 和 Access Key 安全，不要将其提交到代码仓库中。
> *   **不稳定性**: 功能可能会随时调整或重构。

**个人信息的"智能垃圾桶"**——无筛选、无结构、多模态涌入，系统自动处理、检索、清理。

## 1. 项目定位

PersonalBrain 是一个旨在处理海量个人信息的系统。它允许用户随意丢入文本、图片、音频等文件，系统会自动进行去重、语义索引、垃圾识别和归档，从而实现"扔进即忘，需时即查"的体验。

## 2. 核心功能

*   **多模态摄入**: 支持文本、图片、音频等多种格式文件的无缝录入。
*   **语义搜索**: 基于自然语言的跨模态搜索，利用向量数据库进行近似最近邻检索。
*   **自动去重**: 通过内容哈希识别并剔除重复文件。
*   **智能对话**: 提供基于 Chainlit 的对话界面，支持 RAG (检索增强生成)。
*   **可视化管理**: 提供基于 Streamlit 的后台管理仪表盘，用于数据管理和系统配置。
*   **本地化与隐私**: 所有数据本地存储，无需联网即可使用核心功能。
*   **MCP 支持**: 支持 Model Context Protocol，可作为工具集成到 Claude Desktop 或 Cherry Studio。

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

### 3.4 启动应用

本项目包含两个主要界面：

#### 1. 智能对话助手 (Chat Interface)

这是用户的主要入口，提供类似 ChatGPT 的对话体验，可以查询笔记、上传文件。

```bash
python start_app.py
```
*启动后会自动打开浏览器访问 Chainlit 界面。*

#### 2. 后台管理仪表盘 (Admin Dashboard)

用于管理知识库文件、查看系统状态、测试向量搜索效果以及配置模型参数。

```bash
python start_admin.py
```
*启动后会自动打开浏览器访问 Streamlit 管理界面。*

### 3.5 命令行工具 (CLI)

你也可以使用命令行进行快速操作：

```bash
# 导入文件或文件夹
python -m personal_brain.cli ingest "path/to/your/file.txt"

# 语义搜索
python -m personal_brain.cli search "关于人工智能的笔记"

# 垃圾清理预览
python -m personal_brain.cli cleanup --dry-run
```

## 4. Model Context Protocol (MCP) 服务器

PersonalBrain 支持 MCP 协议，可以作为工具被 Claude Desktop、Cherry Studio、Trae 或其他支持 MCP 的客户端集成。

### 功能
- `search_notes`: 搜索笔记。
- `ask_brain_agent`: 基于 RAG 回答问题。
- `ingest_content`: 导入新内容。

### 连接方式

#### 方式一：SSE 模式 (推荐 / Cherry Studio)

**1. 启动服务器**

运行项目根目录下的 `run_mcp_sse.bat`，或者在终端执行：

```bash
python mcp_server.py --transport sse --host 0.0.0.0 --port 8000
```

服务器启动后，SSE 端点地址为：`http://localhost:8000/sse`

**2. 配置客户端 (以 Cherry Studio 为例)**

1.  打开 Cherry Studio 设置 -> 助手/工具 -> MCP 服务器。
2.  添加新服务器：
    *   **类型**: `SSE`
    *   **URL**: `http://localhost:8000/sse`
    *   **名称**: `personal-brain` (任意)

> **注意**: 请确保在启动服务器的终端或 `.env` 文件中配置了 `DASHSCOPE_API_KEY` 环境变量。

#### 方式二：Stdio 模式 (Claude Desktop)

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
*请根据实际情况修改 python 解释器和脚本的路径。*

## 5. 架构设计

系统分为三层：
1.  **Raw Storage (原始存储)**: 原始文件副本，按日期归档。
2.  **Semantic Index (语义索引)**: SQLite + sqlite-vec，存储文本提取、向量嵌入和元数据。
3.  **Application Layer (应用层)**:
    *   **Chainlit App**: 用户对话交互。
    *   **Streamlit Admin**: 系统管理与监控。
    *   **MCP Server**: 外部工具集成。
