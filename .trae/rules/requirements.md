# PersonalBrain 需求文档 (PRD) v3.1

> **重要提示**：本文档定义了项目的核心需求和方向。严禁 AI 在未经用户明确讨论和确认的情况下擅自修改本文档中的需求。

## 1. 项目概况 (Project Overview)
PersonalBrain 是一个基于 "对话即界面" (Conversation as Interface) 和 "读写一体" (Read-Write Integration) 理念的个人知识库系统。用户通过自然语言与 AI 交互，完成知识的记录、整理、检索和回顾。系统旨在成为用户的第二大脑，帮助用户捕捉灵感、管理文件并发现知识间的关联。

## 2. 核心交互范式 (Core Interaction Paradigm)
*   **对话即界面**: 用户的所有操作（写入、搜索、管理）均通过聊天窗口完成。
*   **读写一体**: 写入与检索在同一上下文中进行，系统能根据对话内容自动判断意图。
*   **多模态支持**: 支持文本、文件（PDF/Markdown/图片等）的混合输入与处理。

## 3. 功能需求 (Functional Requirements)

### 3.1 写入与记忆 (Write & Memorize)
*   **意图识别**: Agent 自动识别用户意图（记录 vs 闲聊）。
*   **多模态记录**:
    *   **文本**: 直接记录为笔记 (`entry`)。
    *   **文件**: 上传文件自动进行 OCR/解析，生成 `file_id` 并与笔记关联。
*   **自动图谱构建**: 写入内容时，后台自动提取实体（人名、项目、技术等）及其关系，存入知识图谱。
*   **上下文关联**: 记录的笔记应关联当前对话会话 (`conversation_id`)。

### 3.2 搜索与回顾 (Search & Recall)
*   **混合检索**:
    *   **语义搜索 (`search_semantic`)**: 基于向量相似度检索笔记和文件内容。
    *   **图谱搜索 (`search_graph`)**: 基于实体关系检索（如 "张三参与了哪些项目"）。
*   **时间感知**: 支持自然语言时间查询（如 "上周"、"去年3月"），Agent 需将其转换为 ISO8601 时间范围进行过滤。
*   **类型过滤**: 支持按类型筛选（仅文件、仅笔记、混合）。

### 3.3 整理与维护 (Organize & Maintain)
*   **内容更新**: 用户可通过对话修正或补充现有笔记 (`update_entry`)。
*   **主动建议**: (未来规划) Agent 可基于规则建议清理低质量数据。

### 3.4 审计与透明度 (Audit & Transparency)
*   **思维链展示**: 在界面上展示 Agent 的思考过程（工具调用、中间步骤）。
*   **审计日志**: 记录 Agent 的所有操作（Query, Tool Calls, Results）至数据库 (`agent_audit_logs`)。

## 4. 非功能需求 (Non-Functional Requirements)
*   **架构**: 保持 Python 单体架构，核心逻辑在 `personal_brain` 包中。
*   **界面**: 使用 Chainlit 提供 Web 聊天界面。
*   **存储**: 使用 SQLite 作为统一存储（关系型数据 + `sqlite-vec` 向量索引）。
*   **模型**: 对接兼容 OpenAI 接口的大模型（如阿里云百炼 DashScope）。

## 5. 数据模型 (Data Models)
*   **Conversations**: 存储对话会话元数据 (`id`, `title`, `summary`)。
*   **Entries**: 存储核心记忆/笔记，关联 `conversation_id`。
*   **Files**: 存储文件元数据及 OCR 内容。
*   **Entities/Relations**: 存储知识图谱数据。
*   **AgentAuditLogs**: 存储 Agent 操作审计日志。

## 6. 约束条件 (Constraints)
*   **技术栈**: Python 3.10+, Chainlit, SQLite (with sqlite-vec), OpenAI SDK.
*   **环境**: Windows (主要开发环境), Linux (部署目标).

## 7. 未来规划 (Future Enhancements)
*   **Web 搜索集成**: 允许 Agent 联网搜索以补充知识。
*   **主动整理 Agent**: 后台定期运行任务，自动整理碎片化笔记。
*   **多用户支持**: 虽然目前设计为单用户，但数据库层面预留用户区分能力。
