# Transcript Toolbar + Autosave Implementation Plan

> **For agentic workers:** Implement task-by-task against `docs/superpowers/specs/2026-11-26-transcript-toolbar-autosave-design.md`.

**Goal:** 文稿工具栏重构、自动保存、转写输出格式 TXT/MD、转写后可选自动 AI。

**Architecture:** 后端透传 `output_format` 落盘扩展名；版本 `kind` 区分操作；前端 debounce 自动保存 + 轻量导出/打印/历史/属性。

**Tech Stack:** FastAPI, WorkspaceLibrary, Studio `index.html`

## Global Constraints

- 导出 PDF/Word 仅浏览器轻量方案
- 自动保存，无手动保存主按钮
- `auto_ai` 仅前端执行

---

### Task 1: Backend output_format + kind + props

- [ ] `pipeline.transcribe(..., output_format=)` 写 `.txt`/`.md`
- [ ] `tasks` / `web` 请求字段透传
- [ ] `library.save_edited_transcript(..., kind=)`
- [ ] `GET /api/videos/{id}/transcript/props`
- [ ] index 扫描 `.md`

### Task 2: Studio UI

- [ ] 识别区：输出格式 + 自动 AI 勾选
- [ ] 工具栏：AI / 另存为 / 导出 / 历史 / 打印 / 属性
- [ ] 自动保存 + 历史抽屉 + 属性模态 + 打印样式
