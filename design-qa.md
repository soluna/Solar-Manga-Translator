# 审校工作台交互重构 Design QA

- QA 日期：2026-08-01
- 范围：审校页交互与布局重构，不新增业务能力
- 主目标：保留右侧同页完整文本框列表，集中高频样式操作，减少重复按钮和上下翻找

## Source of truth

- 单选原型：`docs/prototypes/review-workspace-inline-list.jpg`
- 多选原型：`docs/prototypes/review-workspace-multi-list.jpg`
- 交互说明：`docs/professional-review-workspace-interaction-redesign-2026-07-31.md`
- 实现：`frontend/src/AppV2.vue` 与 `frontend/src/styles-v2.css`

## Visual comparison

| State | Reference | Current-run screenshot | Result |
| --- | --- | --- | --- |
| 单选框 | 1440×1024 | `frontend/test-artifacts/v2-workspace/v2-review-interaction-single.png` (1440×1024) | 通过 |
| 高级样式 | 单选框内联展开 | `frontend/test-artifacts/v2-workspace/v2-review-interaction-advanced.png` (1440×1024) | 通过 |
| 多选框 | 1440×1024 | `frontend/test-artifacts/v2-workspace/v2-review-interaction-multi.png` (1440×1024) | 通过 |
| 紧凑桌面宽度 | 桌面工作台 | `frontend/test-artifacts/v2-workspace/v2-review-interaction-1280.png` (1280×900) | 通过 |

对照判断：实现与原型保持一致的顶部命令带、左侧页面轨道、中央 2–3 视图对照区和右侧连续列表。单选时仅活动卡展开，多选时全部卡片保留且收起编辑表单；色彩、边框、间距和信息密度延续现有产品视觉语言。测试夹具仅含 3 个文本框，所以列表长度与 18 框原型不同，但列表结构和滚动模型一致。

## Interaction QA

- 右侧六个现有筛选项均保留，单选、多选和清除多选前后文本框数量不变。
- 单选工具条可直接修改字体、字号和排版方向。
- “复制全部样式”会持续显示来源样式摘要；“粘贴全部样式”已验证字体、字号和启用状态语义。
- 高级样式在当前文本框内联展开，不使用遮挡画布的全局浮层。
- 多选工具条仅包含原有六项批处理：启用、停用、纵排、横排、套用字体、套用字号；右侧和画布不重复出现同类按钮。
- 保留现有 2–3 幅联动对照画布、显式保存和全部既有页级命令。
- 已在 Codex 内置浏览器中手工走查主路径：打开项目、选框、复制样式、切换文本框、粘贴样式、展开/收起高级样式。

## Layout, accessibility, and resilience

- 1440×1024 与 1280×900 均无页面级横向滚动，右侧列表和中央画布保持可见。
- 状态不仅依赖颜色：选择、启用、排版方向和样式剪贴板均有文字表达。
- 主要控件使用原生 button/input/select 与现有 aria-label，复制前粘贴按钮明确禁用。
- 多选来源字号、样式粘贴命令内容、文本框数量和页面溢出均由 E2E 断言覆盖。

## Automated verification

- `npm run build`：通过。
- `npm run test:review-interaction`：通过，覆盖单选、样式复用、高级样式、多选和 1280px 布局。
- `npm run test:v2-workspace`：通过，无浏览器控制台错误。
- 其余前端状态、持久化、字体预览、工作流与连接回归脚本：通过。
- 后端全量：407 passed + 289 subtests passed；3 项失败均来自当前本地上游 `manga_ocr`/供应商参数模块缺失，与本次前端交互改动无关，本次未改动这些用户已有的后端文件。
- `git diff --check`：通过。

## Findings and disposition

- 已发现并修复：右侧文本框数量较少时，Grid 默认分配剩余高度导致卡片被拉伸。修复后卡片以内容高度连续排列。
- 未发现未解决的交互阻断、布局遮挡、溢出或原型能力偏离。

final result: passed
