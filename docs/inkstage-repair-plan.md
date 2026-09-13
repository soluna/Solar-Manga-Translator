# InkStage workflow repair and acceptance

Status: implementation in progress. This plan tracks the full September 2026 review, including usability improvements; passing a smaller smoke test does not close an item.

The new entry keeps its theme and page organization. The existing backend Page Document and project artifact contracts remain authoritative. Frontend adapters may project those documents for editing, but mock data must use the same wire shapes. Existing user projects, outputs, credentials and the separate upstream checkout are preserved.

## 评审方向

新版值得保留：首页、页列表、审校、修图、术语表和设置分工更明确；主题令牌和公共组件给界面一致性提供了基础。旧版值得继承：批量审校、字体预览、工作区调节等已经围绕长时间处理整本漫画展开。优化方向是保留新结构、补齐旧能力、首先保证真实工作流可靠。

不能只按首页是否好看来判断改版完成。这个项目的核心体验是连续处理很多页，随时知道哪些结果有效、哪些编辑已保存、接下来检查哪里。P0 解决信任，P1 减少逐框逐页的重复操作，P2 完善密度、提示和键盘操作。此结论来自项目文档与新旧源码对比；当前尚无获准重新截取的 UI 证据，视觉结论保留。

## Ownership and sequence

- Root: plan, API/task/project integration, import/pages/history, shared UI, backend safeguards, contracts, independent verification, PR and merge.
- Luna Max — editor: canonical document projection, saves/history, live preview, OCR, batch operations, review navigation.
- Luna Max — configuration: real settings/onboarding and glossary/evidence workflow.
- Luna Max — erase: accurate geometry, local/online flows, preview/apply, blank-page brush editing.

Each workstream owns separate files. Root reviews their changes before integration. Critical behavior gets a failing public-interface regression followed by its fix; tests must not only match source strings or invented mock shapes.

## 执行大纲与边界

1. **P0，先可靠**：真实数据契约、保存与冲突恢复、任务连接、设置/首次启动、擦除坐标和预览。
2. **P1，再高效**：原文与 OCR 修正、批量操作、字体预览、按页重嵌字、人工审校标记、跨页任务与工作位置恢复。
3. **P1/P2，最后收口**：可调整工作台、主题与键盘、首页/历史和导入反馈；完成新旧流程验收后提 PR、检查 CI、合并主分支。

交给 Luna Max 的每批任务只需明确范围、依赖和验收结果。不要重做视觉方向，不要用 Mock 成功代替真实接口，不要把已保存/已嵌字/已审校混为一谈，不要覆盖较新的编辑，不要把在线修图标成本地处理，不要修改用户项目、输出或上游仓库的既有工作。主 agent 负责交叉验证与最终合并。

2026-09-13：Luna Max 三路继续收口后端持久化、审校工作流与其他页面易用性。简明执行边界见 `inkstage-luna-outline.md`；主 agent 负责真实 API 联动、回归检查与 PR。未完成的界面验收不算通过。

## 已取得的证据

- `scripts/check_editor_contract.py`：真实 Vue 编辑模块连接 FastAPI handlers，生成素材和临时数据目录；真实前端 multipart 表单的单图、多图、文件夹、ZIP/CBZ 导入；文字/字体/样式往返、撤销重做、禁用恢复、创建删除、合并撤销、项目重载、源文修正、OCR 成败与结果撤销重做、人工审校标记重载及修补后失效、设置脱敏持久化、画笔像素变化、局部擦除、整页预览与应用通过。OCR、掩码检测、修补模型和 provider 由确定性边界替身代替，运行时安装补丁在测试中禁用；不代表真实模型/GPU/在线服务验收。
- `frontend-v3/scripts/test-page-editor.mjs`：8 项通过，覆盖延迟请求、失败保留、跨页响应、冲突、历史与图片缓存。
- `backend.tests.test_api_security` 与 `backend.tests.test_file_handler`：29 项通过，包含实际任务订阅恢复、取消接口和归档边界。2026-09-13 完整后端套件执行 457 项，6 项因本机缺少 torch、freetype、py3langid 等运行依赖出错；其余通过。测试前后嵌套上游仓库已有改动的内容哈希一致。
- `npm --prefix frontend-v3 run test:unit`：45 项通过，包含任务启动确认尚未返回时的取消竞态回归。新增 Mock 只读边界检查：未实现的修改/导出明确失败、未知项目不冒充示例项目、任务与页面身份一致。

## Acceptance ledger

| ID | Priority | Required behavior | Evidence required | Status |
|---|---|---|---|---|
| A01 | P0 | Image, folder, ZIP/CBZ and dropped directory import works | Multipart tests and real backend import UI | API evidence recorded; UI pending |
| A02 | P0 | Canonical region IDs, flags, styles and revisions round-trip | Backend-derived fixture and editor integration | API evidence recorded; UI pending |
| A03 | P0 | Glossary load/preview/apply preserves current entries; missing entries cannot clear | API regression and nonempty glossary workflow | API evidence recorded; UI pending |
| A04 | P0 | One task per start; reconnect only subscribes; terminal/cancel stops retries | Fake transport lifecycle plus real task endpoint integration | 45 v3 tests + API bridge; UI pending |
| A05 | P0 | Settings use real fields, preserve stored secrets, validate current draft, survive restart | Settings helpers and backend/UI round-trip | API evidence recorded; UI pending |
| A06 | P0 | Erase click/rect/stroke coordinates and modes match backend | Geometry tests plus backend selection/preview | API evidence recorded; UI pending |
| A07 | P0 | Serial saves, stale-response guards, flush before navigation, failed drafts retained | Delayed/rejected command tests and rapid UI edits | API evidence recorded; UI pending |
| A08 | P1 | Every edit saves and participates in undo/redo; source text/OCR retry available | Editor interaction and reload checks | API evidence recorded; UI pending |
| A09 | P1 | Draft typography previews; current-page and pending-page rerender are explicit | Visual comparison and command target checks | Implementation under final review; UI pending |
| A10 | P1 | Multi-selection, merge and batch typography/flags work | Multi-region interaction checks | Implementation under final review; UI pending |
| A11 | P1 | Per-page artifact status and export validity are accurate | Mixed/stale page fixtures and export test | API evidence recorded; UI pending |
| A12 | P1 | Human review state is separate from saved/rendered state | Persisted mark invalidation and restoration test | API evidence recorded; UI pending |
| A13 | P1 | Task progress and review context survive route changes; next issue/last page work | Navigation/reconnect workflow | Implementation under final review; UI pending |
| A14 | P1 | Local/online erase is explicit; previews apply/cancel; blank brush/restore works | UI/API erase flow with synthetic media | API evidence recorded; UI pending |
| A15 | P1 | Light/dark menus, toasts and core text remain readable | Theme token checks and current screenshots | Implementation under final review; UI pending |
| A16 | P1 | Workspace fits desktop windows, side panels adjustable/collapsible, one pane remains | 1280×720, 1440×900, 1920×1080 screenshots | Implementation under final review; UI pending |
| A17 | P1 | Manual dev/Windows launcher/Electron routing and auth agree | Client tests, launcher checks, supported platform notes | Implementation under final review; UI pending |
| A18 | P1 | Online processing disclosure and scope labels are accurate | UI copy and backend routing review | Implementation under final review; UI pending |
| A19 | P2 | Stable page/region numbering, issue reasons, keyboard names/focus/tooltips | Filter and keyboard interaction tests | Implementation under final review; UI pending |
| A20 | P2 | Compact home/history, last-location continuation, batch blank image feedback, glossary evidence jumps | Navigation and matching checks | Implementation under final review; UI pending |
| A21 | P0 | Mock fixtures and written API contract match real backend | Contract-shape regression, documented limitations | Fixture refresh and API bridge passed |
| A22 | Release | Relevant tests, documented old E2E, new real-backend E2E, clean content review, GitHub CI, PR merged to main | Recorded commands, screenshots, CI and merged PR SHA | UI, CI and merge pending |

## Verification boundaries

Use generated synthetic media and an isolated APP_DATA_DIR. Never use private source books or existing project outputs as disposable fixtures. Real backend workflow tests may substitute the external inference/provider boundary deterministically; record that separately from GPU/model/provider testing.

Browser access was denied during the original audit. A fresh authorization question is pending for this implementation run. Until permission arrives, continue source/API/unit work and do not bypass the denied browser surface.

Before merging, record evidence for each item, including remaining platform limitations. Do not mark this plan complete just because build or CI is green.
