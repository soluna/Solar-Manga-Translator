# Solar Manga Translator — API 契约（frontend-v3 实施准绳）

后端：FastAPI，`backend/main.py`（约 1819 行）。所有 endpoint 前缀 `/api`。
前端 dev 代理：`/api` 与 `/output` → `http://127.0.0.1:8000`（vite.config.js）。
鉴权：可选 Bearer token（`APP_API_TOKEN` 环境变量未设置时无需鉴权）。
WebSocket 鉴权：subprotocol `manga-translator` + `auth.<token>`。

## 通用约定

- 图片端点支持 `?max_side=<n>` 降采样；易变图片用 `?_=Date.now()` 破缓存。
- 错误响应体：`{"detail": "..."}` 或 `{"detail": {"code", "message", ...}}`。
- 页面编辑冲突：HTTP 409，detail 含 `expected_revision/actual_revision/document`。

## 项目视图 GET /api/projects/{id}（隐含于各操作响应的 payload）

核心字段（`build_client_session_payload`，backend/engine/translator.py:3310）：

```jsonc
{
  "session_id": "...",            // === project_id
  "project_head_generation": 0,
  "project_head_revision_id": "",
  "review_mode": "auto",
  "total_images": 3,
  "images": [{ "name", "stored_name", "url", "region_count", "artifact_state": {} }],
  "translated_images": [{ "id", "name", "url", "stored_name" }],
  "workflow_stage": "idle|detecting|detected|translating|translated|...",
  "page_artifacts": { "<page_id>": { "capabilities": {"can_export": bool, ...} } },
  "download_url": "/api/download/<id>" | "",
  "project": { /* project summary，见下 */ },
  "glossary": { "entries": [] },
  "config": {},
  "overrides": { "translation_region_overrides": {}, "...": {} }
}
```

## 项目摘要（列表与卡片）

`_build_project_summary`（translator.py:1149）：`project_id, title, note, review_mode,
created_at, updated_at, page_count, region_count, workflow_stage, cover_image,
latest_snapshot_id, latest_snapshot_kind, latest_snapshot_summary, snapshot_count,
glossary_count, archived`。

- GET /api/projects → `{"projects": [summary...]}`（含 busy 快照字段）
- PATCH /api/projects/{id} → 更新 note/title 等
- DELETE /api/projects/{id} → 删除
- POST /api/projects/{id}/restore → 恢复会话 → 项目视图
- GET /api/projects/{id}/snapshots → `{"snapshots": [{snapshot_id, project_id, created_at, kind, summary, workflow_stage, cover_image, pinned}]}`
- POST /api/projects/{id}/snapshots/{sid}/restore | /pin
- POST /api/projects/{id}/base-images → 批量生成底图（multipart 或 json，见 main.py:920）

## 上传 POST /api/upload (multipart)

- 单文件：`file` 字段（.zip/.cbz/单图）
- 文件夹：`files` 列表 + `relative_paths` 列表 + `folder_name`
- 可选 `review_mode` form 字段
- 响应：完整项目视图（含 session_id → 跳转 /pages/:id）

## 页面

- GET /api/pages/{sid}/{page_id}/document → `{"document": {regions: [...]}}`
  region 字段（以旧前端 AppV2.vue 用法为准）：`id, bbox[x1,y1,x2,y2], source_text,
  translation, recognition_status, recognition_error, translation_status,
  machine_translation, override_translation, override_skip, auto_style/resolved_style,
  font_size, font_family, alignment, direction(horizontal|vertical), ...`
  document 还含 image URL 等字段（consult AppV2.vue 读取方式）。
- 图片：/base-image /source-image /preview-image /translated-image
  （+ max_side）；/ocr-debug；/translation-input-debug
- POST /api/pages/{sid}/{page_id}/commands
  body: `{"config": {}, "commands": [...], "expected_page_revision": N}`
  commands 类型全集（AppV2.vue 实测）：`update_translation, update_region_bbox,
  update_region_font, update_font_size, update_font_style, update_region_style,
  update_text_direction, create_region, delete_manual_region, merge_regions,
  recognize_manual_region`
  → 响应：项目视图（decorate_project_busy_state）或 409 冲突
- POST /api/manual-regions/{sid}：`action: create|delete|merge|recognize` + 各自参数
- POST /api/style-regions/{sid} /review-regions/{sid} /page-regions/{sid}：
  `{"config": {}, "target_stored_name"?}` → 触发检查任务 → 项目视图

## 工作流（任务）—— 走 WebSocket

WS `/ws/translate/{session_id}`：客户端发 JSON 启动任务：
`{"action": "detect|translate|resume-translate|translate-page|rerender|...",
 "config": {}, "target_stored_name"?}`（或传 `task_id` 订阅已有任务）。
服务端随后推送事件流。事件字段（backend/main.py:1715 + task-event-state.js）：
`event(start|progress|status|completed|error|cancelled|interrupted), task_id,
sequence, action, phase_label, phase_index, phase_total, scope_label,
progress_current, progress_total, workflow_step, step_label, step_index,
step_total, message, total_pages, metadata.target_stored_name, ...`

- 任务快照：GET /api/tasks/{task_id}；GET /api/projects/{id}/task
- 取消：POST /api/tasks/{task_id}/cancel
- 动作标签/阶段文案：frontend/src/workflow-state.js（已复制到 src/state/）
- 事件归约：src/state/task-event-state.js 的 `deriveTaskEventUpdate(payload, state)`

## 擦除

- POST /api/pages/{sid}/{page_id}/advanced-erase
  body: `{"action": "erase|...", "config": {}, "selections"?, "selection_strokes"?, "local_mask_mode"?, "attempt_id"?}`
- POST .../advanced-erase/suggest-selection：`{"point": [x,y], "config": {}}` → `{"selection": ...}`
- GET .../advanced-erase/previews/{attempt_id}/{kind} → 预览图（No-Store）
- POST /api/pages/{sid}/{page_id}/brush-edit：`{"operations": [...]}`

## 设置

- GET /api/app/settings → `{"settings": {...}}`（字段集以旧前端 config-persistence.js / AppV2.vue 为准：翻译服务 provider/api_key/model、检测、擦除、渲染等）
- PATCH /api/app/settings → 合并保存 → `{"settings": {...}}`
- POST /api/app/settings/validate → 校验
- GET /api/app/migration-status；POST /api/app/migrate-legacy
- GET /api/status → `{"status": "running", "auth_required": bool}`
- GET /api/fonts；GET /api/fonts/file/{source}/{font_name}
- 运维（收纳进「高级」）：/api/app/diagnostics、/remote-diagnostics start/stop、
  /api/app/remote-execution enable/rotate-token/disable、/logs/tail、open-logs、
  open-data-directory、open-user-fonts、/api/app/local-models/lama-large

## 名词库

- GET /api/projects/{id}/glossary → `{"entries": [...]}`（结构以旧前端为准：term/translation/note/category?）
- PUT /api/projects/{id}/glossary → 整体替换
- POST /api/projects/{id}/glossary/extract → AI 提取候选
- POST /api/projects/{id}/glossary/preview → 替换预览
- POST /api/projects/{id}/glossary/apply → 应用

## 导出

- GET /api/download/{sid} → zip（FileResponse）
- GET /api/download/{sid}/blank → 空页 zip

## 校验要点

- 页面编辑必须带 `expected_page_revision`（页面文档带 revision），409 时刷新文档。
- WS 事件按 `sequence` 去重/排序（task-event-state.js 已处理）。
- 所有操作响应均含项目视图 → 直接刷新本地 project state。
- 图片 URL 全部经过 `withCacheBust` + `withImagePreviewSize`。