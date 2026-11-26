# 文稿工具栏 + 自动保存 + 输出格式设计

> 日期：2026-11-26  
> 状态：待评审  
> 范围：识别页控件、文稿面板工具栏、轻量导出、版本历史与属性

## 背景

Studio 文稿区现有「保存 / AI 纠错 / AI 排版 / 下载 TXT / 复制路径 / 访达 / 导出 MD / 迁移」较散。目标改为更接近文档编辑器的操作，并支持转写输出格式与转写后自动 AI 处理。

已确认决策：

- 导出 PDF / Word 采用**浏览器轻量方案**（非服务端正式生成）
- **自动保存**，不保留手动「保存」主按钮
- 「转写为 Markdown / TXT」指**工作区落盘扩展名**（方案 A）

## 目标

1. 识别区可选输出格式 `TXT | Markdown`，以及「转写完成后自动 AI 纠错 + 排版」
2. 文稿工具栏收敛为：AI 纠错、AI 排版、另存为、导出、历史记录、打印、属性
3. 编辑 / AI / 回滚均产生可回滚版本；自动保存 debounce 写入工作区
4. 属性可查看类型、位置、大小、创建/修改时间、字数等

非目标（本轮不做）：

- 服务端生成正式 `.pdf` / `.docx`
- Telegram 等新平台
- 迁移功能深化（可从工具栏移除或藏到属性外）

## 交互设计

### 识别区（hero controls）

新增：

| 控件 | 行为 |
|------|------|
| 输出格式 | 单选/下拉：`TXT`（默认）/ `Markdown`；写入 `state.outputFormat`，并随转写任务提交 |
| 转写后自动处理 | 勾选：`完成后 AI 纠错 + 排版`；转写 `completed` 且加载文稿后，串行调用 proofread → format，每步自动存版本 |

说明：

- 未勾选时行为与现网一致，只落原文稿
- 自动 AI 失败时 toast 提示，不阻断已完成的转写结果展示
- 批量任务页本轮可复用同一偏好（localStorage），不强制改队列 API

### 文稿工具栏

顺序与能力：

| 按钮 | 行为 |
|------|------|
| AI 纠错 | 现有 `/api/ai/transform` mode=proofread；成功后自动保存新版本（kind 标注） |
| AI 排版 | 同上 mode=format |
| 另存为 ▾ | hover/点击展开：`TXT`、`Markdown`；浏览器下载当前编辑器内容 |
| 导出 ▾ | hover/点击展开：`Word`、`PDF`。Word = 下载 HTML 包装的 `.doc`；PDF = 打开打印预览（`window.print`），由用户选「存储为 PDF」 |
| 历史记录 | 打开面板/抽屉，列出 `/api/videos/{id}/versions`；支持预览与「恢复」→ `POST .../activate` 后刷新编辑器 |
| 打印 | 同打印预览（可与导出 PDF 共用打印样式） |
| 属性 | 模态：类型（扩展名/格式）、位置（绝对路径）、大小、创建时间、修改时间、字数/字符数、当前版本号、引擎等 |

移除主路径上的：手动保存、下载 TXT、导出 MD、复制路径、访达、迁移（路径/访达可放进属性；迁移本轮不做）。

自动保存：

- 编辑器 `input` 后约 **1.2s** debounce 调用现有 `PUT /api/videos/{id}/transcript`
- 保存中/已保存状态用小字提示在路径行旁（如「已自动保存」「保存中…」）
- 无 `videoId` 时不触发

### 历史记录

- 列表展示：版本 id、kind、时间、字符数、是否当前
- 「恢复」将该版本设为 active 并载入文本
- 可选：恢复前若编辑器有未落盘改动，先 flush 一次自动保存（避免丢字）

### 属性

数据来源：

- 当前 active 版本文件 `stat` + `char_count`
- video 元数据（创建时间、引擎、模型）
- 前端可补字数（按空白分词近似）与字符数

新增轻量 API（见下）一次返回，避免前端拼多接口。

## 数据与后端

### 输出格式落盘

- 转写原文、编辑版本文件扩展名跟随 `output_format`：`txt` → `.txt`，`markdown` → `.md`
- `library.save_edited_transcript` / pipeline 写 original 时按格式选后缀
- 索引扫描同时识别 `.txt` 与 `.md`

### 任务请求

`TranscribeTaskRequest` / 批量请求增加可选字段：

```json
{
  "output_format": "txt" | "markdown",
  "auto_ai": false
}
```

- `output_format` 影响服务端落盘后缀与 metadata
- `auto_ai` **本轮由前端在 completed 后执行**（不堵任务线程）；字段可先只做前端 local 偏好，或原样回传便于后续服务端编排

推荐本轮：`output_format` 进 API；`auto_ai` 仅前端偏好（localStorage），避免拉长任务 worker。

### 版本 kind（尽量复用现有字段）

| 场景 | kind |
|------|------|
| 转写原文 | `original` |
| 自动保存 / 手动等价写入 | `edited` |
| AI 纠错后 | `proofread` |
| AI 排版后 | `format` |

`save_edited_transcript` 增加可选 `kind` 参数；默认 `edited`。

### 新/改 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/videos/{id}/transcript/props` | 属性聚合：path、suffix、size_bytes、created_at、modified_at、char_count、word_count、version_id、kind、engine、model、title |
| PUT | `/api/videos/{id}/transcript` | 可选 body.`kind`；落盘扩展名用当前偏好或已有文件后缀 |
| GET | `/api/videos/{id}/versions` | 已有；确保返回 created_at、char_count、kind、is_active |
| POST | `/api/videos/{id}/versions/{vid}/activate` | 已有 |

不新增服务端 PDF/DOCX 生成接口。

## 前端结构（index.html）

- CSS：`.menu-btn` + `.menu-pop`（hover/focus 展开）；`.drawer` / `.modal` 用于历史与属性；`@media print` 隐藏壳层只打文稿
- JS state：`outputFormat`、`autoAiAfter`、`saveTimer`、`saveState`
- 转写完成回调：`loadVideo` → 若 `autoAiAfter` 则 `runAI('proofread')` → `runAI('format')`（均走自动保存）

## 风险与边界

- 国内环境打印「存 PDF」依赖系统打印面板，体验因 OS 而异——属性/说明中可一句提示
- `.doc` 实为 HTML，Word 可开但不是 OOXML；UI 文案用「Word」即可，不承诺复杂排版保真
- Markdown 落盘后编辑器仍是纯 textarea，不提供实时 MD 预览（可列为后续）
- 抖音等平台稳定性与本功能无关

## 验收清单

- [ ] 选 Markdown 转写后，original/edited 文件为 `.md`
- [ ] 选 TXT 时为 `.txt`
- [ ] 勾选自动 AI 后，完成转写会依次纠错、排版，并有多个版本可回滚
- [ ] 编辑停顿后出现自动保存，历史中可见新版本
- [ ] 另存为可下载 txt/md；导出 Word 可下载；导出 PDF/打印弹出打印预览
- [ ] 属性展示路径、大小、时间、字数等
- [ ] 工具栏不再出现独立「保存」「迁移」主按钮

## 实现顺序建议

1. 后端：`output_format` 落盘 + `save_edited_transcript(kind=)` + props API  
2. 识别区控件 + 自动 AI 流水线  
3. 工具栏重构：另存为 / 导出 / 打印 / 自动保存  
4. 历史抽屉 + 属性模态  
5. 说明页或 placeholder 文案微调（可选）
