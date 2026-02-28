# 项目进度日志 (Project Progress Log)

> **重要提示**：在完成一项任务或重要步骤后，AI 应更新本文档，详细记录已完成的内容，包括日期、任务描述、状态以及任何备注或遇到的问题。

## 进行中的任务 (Active Tasks)
- [ ] **用户体验优化**: 进一步优化 Chainlit 界面的交互细节（如更友好的错误提示、更丰富的 Markdown 渲染）。
- [ ] **知识图谱可视化**: 在 Admin Dashboard 中增加图谱的可视化展示。

## 已完成任务 (Completed Tasks)

| 日期 (Date) | 模块 (Module) | 任务 (Task) | 详情 (Details) |
|---|---|---|---|
| 2026-02-24 | **Database** | **Schema V3 升级** | 核心表结构 `conversations` (会话), `entries` (笔记), `files` (文件), `agent_audit_logs` (审计), `entities`/`relations` (图谱) 已全部实现。 |
| 2026-02-24 | **Ingestion** | **多模态解析管道** | 1. **PDF**: 集成 MinerU + Aliyun OSS，支持高精度 OCR 解析。<br>2. **图片**: 集成 Qwen-VL-Plus，实现视觉理解与文本提取。<br>3. **音频**: 集成 Qwen-ASR，支持长音频转写与自动清理。 |
| 2026-02-24 | **Search** | **混合检索系统** | 1. **语义搜索**: 基于 `sqlite-vec` 实现向量检索，支持时间范围 (`start/end`) 和类型 (`file`/`entry`) 过滤。<br>2. **重排序**: 集成 `qwen3-vl-rerank` 模型，对检索结果进行二次排序优化。<br>3. **图谱搜索**: 实现 `search_graph` 工具，支持实体关系查询。 |
| 2026-02-24 | **Agent** | **核心逻辑与工具** | 1. **工具集**: `read_document` (全文读取), `search_semantic` (语义检索), `write_entry` (写入), `update_entry` (更新), `delete_entry` (删除)。<br>2. **审计**: 完整记录 Tool Calls 和思考过程至 `agent_audit_logs`。<br>3. **上下文**: 支持 Conversation History 注入和 Long Context 处理。 |
| 2026-02-24 | **UI** | **Chainlit 交互** | 1. **会话管理**: 支持多轮对话、历史记录持久化与恢复 (`on_chat_resume`)。<br>2. **文件交互**: 支持拖拽上传自动触发 Ingestion，侧边栏文件列表 (`/side`) 及删除功能。<br>3. **反馈机制**: 流式响应 (Streaming) 与引用来源 (Sources) 展示。 |
| 2026-02-25 | **UI** | **References 可点击查看** | `search_semantic` 返回结果增加 `ref_type/ref_id`，Chainlit 引用列表改为输出可点击链接，并新增 `/ref/{type}/{id}` 路由用于打开查看对应 entry/file/chunk 内容。 |
| 2026-02-25 | **UI** | **References 点击打开内容** | 由于部分区域不支持可点击超链接，改为在回答消息下方渲染引用按钮（Actions）；点击按钮通过回调查询数据库，并在侧边栏展示对应 entry/file/chunk 内容（必要时截断预览）。 |
| 2026-02-25 | **UI** | **References 内置引用按钮** | 引用按钮调整为显示在 `📚 References` 折叠区内部（作为其子消息），避免占用主回答区域；点击按钮仍可在侧边栏打开引用内容。 |
| 2026-02-26 | **UI** | **References 折叠区内可点击链接** | 在 `📚 References` 折叠区内部渲染一条子消息，包含 Markdown 链接列表（指向 `/ref/{type}/{id}`）。不再在主对话区生成额外气泡，点击在新标签页打开内容。 |
| 2026-03-01 | **UI** | **Chunk 导出功能** | 1. **Chainlit**: 在知识库内容预览页 (`/ref/...`) 增加 "Export to File" 按钮。<br>2. **Admin Console**: 在知识库 Chunk Viewer 中增加 "📥 Export to File" (单个) 和 "📦 Export All Chunks" (批量) 按钮。 |
| 2026-03-01 | **Ingestion** | **优化语义切分** | 1. 废弃基于关键词的切分合并策略。<br>2. 引入 LLM (`_refine_structure_with_llm`) 对 Markdown 标题结构进行语义分析，智能识别并合并从属章节（如 "Notes"、"Warnings"）。 |

## 待办事项 (Backlog)
- [ ] **Web 搜索能力**: 集成联网搜索工具 (如 Serper/Google Search) 以补充外部知识。
- [ ] **主动整理 Agent**: 实现后台定期运行任务 (Gardener)，自动整理碎片化笔记并优化图谱。
- [ ] **性能优化**: 针对大量文件场景下的向量检索速度优化 (考虑 HNSW 索引参数调优)。
- [ ] **多用户支持**: 虽然数据库已预留字段，但应用层尚未实现多用户隔离逻辑。
