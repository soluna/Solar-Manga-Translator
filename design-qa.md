# 审校工作台交互重构 Design QA

- QA 日期：2026-08-02
- 范围：正式审校页交互与布局重组，不新增业务能力
- 主目标：页级命令回到右上，对照视图居中，单框样式操作收回当前卡片，所有文本框始终保留在同一列表

## Source visual truth and rendered evidence

- 源视觉：`docs/prototypes/review-workspace-html-v3-1280.png`（1280×900，三视图、当前框、高级样式展开）。
- 正式实现：`frontend/test-artifacts/v2-workspace/v2-review-interaction-1280-advanced.png`（1280×900，同状态）。
- 右栏聚焦源图：`frontend/test-artifacts/v2-workspace/v2-review-prototype-1280-right.png`（390×900）。
- 右栏聚焦实现：`frontend/test-artifacts/v2-workspace/v2-review-production-1280-right.png`（390×900）。
- CSS viewport 与截图像素一致，device scale factor 1，无密度归一化。
- 实现文件：`frontend/src/AppV2.vue`、`frontend/src/styles-v2.css`、`frontend/scripts/test-v2-workspace-e2e.mjs`。

## Full-view comparison evidence

- 顶部第二层已稳定为三列：对照开关相对整个工具栏精确居中，页级命令靠右；单框字体、字号、方向和样式迁移不再占用顶部。
- 1280px 下保留 76px 页面轨道、390px 右侧文本框列表和中央三幅联动画布，无页面级横向溢出。
- 右侧六个现有筛选保持同一行；活动框在列表原位展开，其他框不被详情页或抽屉替换。
- 实例中的漫画夹具内容与概念原型的页面缩放状态有差异，但工具层级、三栏比例、右栏密度和关键交互状态已对齐。

## Focused right-panel comparison evidence

- 字体与字号保留在当前卡内；“字体应用到本页”和“字号应用到本页”已移除。
- 高级样式分为“几何与间距”和“颜色与底图”，保留旋转、描边强度、字距、行距、8 个描边预设、两组色板/拾色器/Hex 以及保留底图。
- 底部四个 Lucide 图标按钮保持单行：复制全部样式、粘贴全部样式、复制文本框、更多与高级样式；每个按钮同时具有 aria-label、title 和 hover 文案。
- 展开高级样式后操作行自动对齐到最近可见位置，1280px 断言确认四按钮仍在右栏可见范围内。

## Required fidelity surfaces

- 字体与排版：正式页延续产品现有系统字体、层级和密集工具文本；390px 右栏下筛选、字体名和高级样式标签无异常截断。
- 间距与布局：三列顶部、76/390px 侧栏、高级样式分组和四图标操作行与原型主结构一致。
- 颜色与视觉 token：复用产品现有蓝色选中、绿色样式剪贴板、灰色描边与白色面板 token；禁用、选中和展开状态均可辨。
- 图片与资产：画布和缩略图使用真实测试夹具图像；新操作图标使用 `lucide-vue-next`，没有新增手绘 SVG、文本符号或占位资产。
- 文案与内容：样式摘要明确显示“含高级样式”；分组文案说明“即时预览”与“跟随完整样式复制”。

## Interaction and automated verification

- Codex 内置浏览器：打开项目、进入审校、复制全部样式、确认粘贴启用、展开高级样式、确认全部字段和四图标操作可达；页面布局测量为对照开关居中偏差 0px、右栏 390px、无水平溢出。
- 样式粘贴 E2E：验证字体、字号、启用状态、旋转、描边、字距、行距、前/背景颜色和保留底图均进入 `update_region_style` 命令。
- `npm run build`：通过。
- 10 组前端基础回归：通过。
- `npm run test:review-interaction`：通过。
- `npm run test:v2-workspace`：通过，`consoleErrors: []`。
- `python -m unittest discover backend/tests -v`：410 项全部通过。
- 桌面端 5 个入口脚本 `node --check`：全部通过。

## Comparison history and findings

- Pass 1，P2：旧的 1280/1380px 断点将右栏缩到 340/360px，导致六个筛选换行、高级样式纵向拉长。已在两个断点均恢复 390px。
- Pass 1，P2：展开高级样式后使用平滑滚动，E2E 证明动画结束前操作行可能暂时位于右栏可见范围外。已改为立即 `scrollIntoView({ block: 'nearest' })`。
- Pass 2：1280×900 三视图展开态及 390×900 右栏聚焦对照中，上述问题均已消失；无剩余 P0/P1/P2。
- P3，接受：正式页保留产品实际状态提示和更鲜明的字重，比抛弃式原型稍密、稍强；这是复用现有设计系统的有意差异，不影响核心任务。

final result: passed

---

# HTML 原型高级样式补全 Design QA（2026-08-02）

## Evidence

- 视觉基线：`docs/prototypes/audit-advanced-style-01-collapsed.png`（1600×1000）。
- 功能基线：`frontend/src/AppV2.vue:11886-12029`，包含旋转、描边、间距、文字色、底/描边色和保留底图。
- 实现折叠态：`docs/prototypes/review-workspace-html-v3-collapsed.png`（1600×1000）。
- 实现展开态：`docs/prototypes/review-workspace-html-v3-advanced.png`（1600×1000）。
- 紧凑桌面展开态：`docs/prototypes/review-workspace-html-v3-1280.png`（1280×900）。
- CSS viewport 与截图像素一致，device scale factor 1；无密度归一化。

## State and comparison

- 折叠态源图与实现图 SHA-256 完全一致，说明补全高级功能没有改变原有画布、顶部层级、右侧卡片或四图标操作行。
- 展开态按正式产品字段补齐：四个数值输入、八个描边快捷档位、两组预设色、两个颜色选择器、两个 Hex 输入和保留底图开关。
- 1600×1000 与 1280×900 原始像素截图中的 390px 右侧栏均足以直接检查密集控件，因此未另做裁切对比。

## Required fidelity surfaces

- 字体与排版：继承原型现有字体、字重和 9–10px 密集工具文本；标签无截断或异常换行。
- 间距与布局：高级区按两组组织，双列输入和紧凑色板保持原卡片节奏；折叠态零视觉漂移。
- 颜色与状态：复用现有蓝色焦点、绿色成功和正式产品六个颜色预设；禁用、选中与焦点状态可见。
- 图片与资产：漫画页面与缩略图继续使用原有仓库截图资产，未改动裁切或质量。
- 文案与内容：字段名称和取值范围与正式产品一致；样式剪贴板增加“含高级样式”。

## Interaction verification

- 展开/收起高级样式：通过。
- 数值编辑和范围限制：通过。
- 描边快捷值：通过。
- 文字色、底/描边色预设与 Hex 同步：通过。
- 保留底图：通过。
- 复制完整样式后修改旋转、描边、文字色和保留底图，再粘贴恢复：通过。
- 浏览器控制台：0 error，0 warning。

## Comparison history

- Pass 1，P1：旧展开态只有四个只读数值，遗漏颜色、底图和全部编辑能力。已补齐正式产品现有字段与交互。
- Pass 2，P2：1280px 展开后，四图标操作行位于滚动视口底部之外（操作行 top 868，滚动视口 bottom 860）。已将展开后的自动滚动目标改为操作行。
- Pass 3：1280px 复查中操作行 top 828、bottom 860，与滚动视口 bottom 860 对齐；未发现剩余 P0/P1/P2。

final result: passed

---

# HTML 原型修订 Design QA（2026-08-02）

- 范围：`docs/prototypes/review-workspace-redesign-concepts.html?screen=single`
- 视觉来源：用户提供的现行审校页截图 `codex-clipboard-274e1571-49be-42a4-ac91-b18eafb60124.jpg`（3840×2088）
- 本轮截图：`docs/prototypes/review-workspace-html-v2.png`（1600×1000）
- 约束：仅重排现有交互，不引入新的业务能力

## Comparison result

- 顶部层级已从“左上角混合页级命令和单框样式”调整为“中央对比视图 + 右侧页级命令”；项目级操作仍位于最上方右侧。
- 单框的字体、字号、启用状态、排版方向全部收回当前展开卡片；顶部不再出现单框格式工具。
- “字体应用到本页”“字号应用到本页”未出现在原型；样式迁移统一为当前卡内的“复制全部样式 / 粘贴全部样式”。
- 当前框底部四项操作已压缩为同一排图标按钮；按钮顺序保持为复制样式、粘贴样式、复制文本框、高级样式，并通过悬停文案与可访问名称解释动作。
- 右侧保持所有文本框在同一连续、可滚动列表中，活动框仅在原位置就地展开，没有引入详情页或抽屉替换列表。
- 中央仍保持三视图并排和联动画布信息，左侧页缩略图、搜索、筛选、保存、导出和既有页级命令均保留。

## Interaction checks

- 单框状态与多框批处理状态可通过原型底部切换器互相进入。
- “复制全部样式”会展示剪贴板摘要并启用“粘贴全部样式”；粘贴反馈已验证。
- “更多与高级样式”可在当前卡内展开和收起。
- 四个紧凑按钮的 tooltip、禁用态、成功态和键盘焦点态已检查。
- “已启用 / 纵排”状态按钮可独立切换。
- 浏览器控制台：0 error，0 warning。

## Findings

- P2，已修复：画布状态文案仍写为两视图联动，与三视图结构不一致；已改为“3 个视图联动画布”。
- P0/P1：无。
- 未解决的 P2/P3：无。

final result: passed
