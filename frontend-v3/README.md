# Solar · InkStage — 新前端（v3）

> 全新入口：`frontend-v3/`（Vue 3 + Vite），与旧前端 `frontend/` **完全独立**。
> 旧入口保持不变；新入口通过不同端口进入，互不影响。

## 快速开始

```bash
cd frontend-v3
npm install          # 首次
npm run dev          # 开发服务器（默认 http://127.0.0.1:5273/）
```

- 后端需同时运行（`start.mac.sh` / `start.sh`，或单独 `python backend/desktop_server.py`，默认 8000 端口）。
- dev server 自动代理 `/api` 与 `/output` 到 `http://127.0.0.1:8000`。
- 端口可用环境变量覆盖：`VITE_DEV_PORT=5274 npm run dev`。

### 无后端预览（mock 模式）

```bash
npm run dev:mock     # 打开 http://127.0.0.1:5273/ 即可浏览全部页面
```

mock 模式由 `plugins/mock-api.js` 拦截 `/api/**` 返回契约形状的假数据
（示例项目 session_id=`demo`，可直接访问 `#/pages/demo`、`#/review/demo/c002.png`）。
仅用于视觉与交互验证，不代表后端真实行为。

## 页面地图（hash 路由）

| 路由 | 页面 |
|---|---|
| `#/` | 首页 / 上传（文件、文件夹、zip/cbz） |
| `#/pages/:sessionId` | 页面列表 + 三步工作流（识别→翻译→审校嵌字） |
| `#/review/:sessionId/:pageId` | **审校工作台**（多视图画布 + 文本框编辑） |
| `#/erase/:sessionId/:pageId` | 擦除工作台（整页/选区、点击/框选/画笔） |
| `#/projects` | 项目管理（快照恢复/重命名/删除） |
| `#/glossary/:sessionId` | 专有名词库（AI 提取/预览/应用） |
| `#/settings` | 设置（翻译服务/检测/渲染/字体/高级运维） |
| `#/onboarding` | 首次启动引导 |

## 目录结构

```
frontend-v3/
├── docs/API-CONTRACT.md      # 后端 API 契约（实施准绳）
├── plugins/
│   ├── mock-api.js           # mock 模式 Vite 插件
│   └── mock-data.mjs         # mock 数据（形状贴近真实契约）
└── src/
    ├── api/client.js         # fetch 封装 + Bearer token + WS 鉴权 + 图片 URL 工具
    ├── composables/
    │   ├── useProject.js     # 项目视图加载/采纳（POST restore）
    │   ├── useTaskEvents.js  # WS 任务事件（启动任务/订阅/重连/sequence 去重）
    │   └── useToast.js       # 极简 toast
    ├── state/                # 自旧前端复制、后端验证过的状态归约（只读使用）
    │   ├── workflow-state.js
    │   ├── task-event-state.js
    │   ├── review-workspace-state.js
    │   └── region-typography.js
    ├── assets/               # Solar · InkStage 设计系统（深色优先，可切浅色）
    └── views/                # 8 个页面
```

## 设计系统速查

- 深色优先：`:root` 暗色、`[data-theme=light]` 亮色（右上 🌙/☀️ 切换，记忆于 localStorage `solar-theme`）。
- 主操作/激活 = teal `#3ECFC0`；需留意 = 琥珀 `#E8A33D`；完成 = 绿；AI = 紫。
- 工程细节用等宽字体（坐标/页码/尺寸）；网点纹理致敬漫画制版。
- 全局类名来自 `src/assets/theme.css` + `src/assets/app.css`（自设计稿原样迁移）。

## 与旧前端的关系

- 新入口不读取、不修改、不依赖旧前端任何资源；可同时运行。
- 后端 API 完全复用（同一 FastAPI 服务），改动面为零。