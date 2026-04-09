# Manga Auto-Translator WebUI

基于 manga-image-translator 封装的本地全流程极速翻译画廊。提供极佳的大文件（zip/cbz）自动翻译体验，支持“流式”出图、边译边看，并开放完整的高级配置。充分利用本地独立显卡进行高性能计算加速。本系统为 **Windows + CUDA** 量身打造。

## 核心模块
1. **文本检测**: 识别对话气泡边界 (Bounding Box)
2. **OCR**: 提取高精度日文
3. **翻译**: 可选使用 Google Translate, DeepL, 网易有道，或结合整页语境上下文的 GPT-4 等 LLM 翻译
4. **图像修补 (Inpainting)**: 擦除日文并保留网点纸背景
5. **排版 (Typesetting)**: 中文自动换行与居中渲染
6. **Web UI**: 基于 Vue3 和 Tailwind CSS 构建的现代化画廊界面，支持拖拽压缩包流式处理

## 项目主文档

为了把本项目按一个严肃、可持续演进的产品来推进，后续所有迭代都会统一记录在：

- [<repo>/docs/project-iteration-log.md](<repo>/docs/project-iteration-log.md)

它负责沉淀：

- 项目阶段
- 关键设计决策
- 每轮迭代摘要
- 验证范围
- 已知风险与下一步

待办优先级与功能列表仍继续维护在：

- [<repo>/BACKLOG.md](<repo>/BACKLOG.md)

核心流程体验评估现统一维护在：

- [<repo>/docs/evals/core-flow-experience-scorecard-v1.md](<repo>/docs/evals/core-flow-experience-scorecard-v1.md)
- [<repo>/docs/evals/records/2026-04-03-core-flow-baseline-v1.md](<repo>/docs/evals/records/2026-04-03-core-flow-baseline-v1.md)

它负责沉淀：

- 我们当前到底如何评估“主流程体验”
- 每轮迭代的固定评分口径
- 当前基线分数与最关键短板

## Obsidian 文档同步

本项目的核心文档会同步到你的 Obsidian 目录：


当前同步范围包括：

- [<repo>/README.md](<repo>/README.md)
- [<repo>/BACKLOG.md](<repo>/BACKLOG.md)
- [<repo>/docs](<repo>/docs) 下当前项目自维护的设计、评估与迭代文档

同步脚本：

```bash
cd <repo>
bash ./scripts/sync_obsidian_docs.sh
```

约定：

- 仓库内文档是主版本
- 修改仓库文档后，应同步运行一次脚本，把更新推送到 Obsidian

## 核心流程体验评估（v1）

如果我们的方向是“先把主流程打磨到极致，而不是继续堆功能”，那每轮迭代都应该先回答：

- 这轮是否让用户更稳定地完成一次完整任务？
- 中途是否更少被打断？
- 预览与最终结果是否更一致？
- 历史项目与异常场景是否更可靠？

当前项目已经建立：

- 评分规则：[<repo>/docs/evals/core-flow-experience-scorecard-v1.md](<repo>/docs/evals/core-flow-experience-scorecard-v1.md)
- P0 修复清单：[<repo>/docs/evals/core-flow-p0-repair-checklist.md](<repo>/docs/evals/core-flow-p0-repair-checklist.md)
- 记录模板：[<repo>/docs/evals/iteration-scorecard-template.md](<repo>/docs/evals/iteration-scorecard-template.md)
- 当前基线：[<repo>/docs/evals/records/2026-04-03-core-flow-baseline-v1.md](<repo>/docs/evals/records/2026-04-03-core-flow-baseline-v1.md)
- 当前正式评估：[<repo>/docs/evals/records/2026-04-03-core-flow-current-formal-eval-v1.md](<repo>/docs/evals/records/2026-04-03-core-flow-current-formal-eval-v1.md)
- 本地画布回归：[<repo>/docs/evals/local-canvas-regression.md](<repo>/docs/evals/local-canvas-regression.md)
- Windows 桌面分享版发布说明：[<repo>/docs/windows-desktop-release.md](<repo>/docs/windows-desktop-release.md)

如果要开始新一轮评分卡记录，可以直接运行：

```bash
cd <repo>
python3 ./scripts/create_eval_record.py --slug core-flow-iteration
```

## 安装与启动 (一键运行)
为了方便在 Windows 上使用，我们提供了一键启动脚本 `start.bat`。

## Windows 桌面分享版（新）

如果目标是把应用分享给其他人双击使用，现在仓库里已经新增了桌面壳骨架：

- Electron 壳目录：[<repo>/desktop](<repo>/desktop)
- 发布文档：[<repo>/docs/windows-desktop-release.md](<repo>/docs/windows-desktop-release.md)

这套新结构已经做了这些准备：

- 用户数据与应用目录分离
- 设置持久化迁移到用户配置文件
- 旧版项目数据迁移提示
- 桌面壳动态注入本地后端地址
- Windows 打包脚本骨架

当前仍建议：

- 开发与本机使用继续走 `start.bat` / `start.mac.sh`
- 真正对外发布时，按桌面版文档在 Windows 机器上构建安装包

### Windows (RTX GPU 推荐)
在资源管理器中双击运行 `start.bat`。脚本会自动执行：
1. 检查 Python 和 Node.js 环境
2. 创建专属虚拟环境 (`venv`)
3. 自动安装适用于 RTX 系列显卡的 PyTorch (CUDA 11.8)
4. 安装 manga-image-translator 及项目的所有后端依赖
5. 安装前端依赖，并先后启动后端 API 和前端 WebUI。

*注意：第一次运行需要下载数 GB 的依赖包（主要是 PyTorch 和 AI 模型），请耐心等待。下载完成后会自动打开浏览器。*

从当前版本开始，Windows 的 `start.bat` 会以“受控会话”方式启动：
- 会拉起一个专用浏览器窗口来打开 WebUI
- 当你关闭这个专用窗口时，后端 API、前端 dev server，以及对应的终端窗口会一起自动关闭
- 如果机器上没有可用的 Edge/Chrome，会回退到默认浏览器打开，此时需要在启动窗口里按 `Ctrl+C` 手动结束服务

### Linux/macOS
打开终端，赋予可执行权限后运行：
```bash
chmod +x start.sh
./start.sh
```

### macOS 本机独立测试环境
如果你希望在 Mac 上做日常开发、UI 回归或 API/工作流测试，同时**不影响 Windows 上的正式使用环境**，推荐改用单独脚本：

```bash
chmod +x start.mac.sh
bash ./start.mac.sh
```

如果你更希望像 Windows 一样直接双击启动，也可以在 Finder 里双击：

- [<repo>/start.mac.command](<repo>/start.mac.command)

第一次双击时，macOS 可能会提示权限或安全确认；允许后就可以直接在 Terminal 里启动整套服务。

这条路径会：
1. 只使用 `backend/.venv-mac`
2. 不会触碰 Windows 使用的 `backend/venv`
3. 在本机启动后端 API 和前端页面，适合做：
   - 前端/画布工作台调试
   - 项目管理、快照、逐框校对工作流验证
   - 翻译请求链路与调试导出验证
   - 后端 API 冒烟测试

说明：
- macOS 这套更适合作为**开发与回归测试环境**
- Windows + CUDA 仍然是当前的正式高性能运行环境
- `start.mac.sh` 依赖 Python `3.10/3.11`，不会使用系统里不兼容的 `3.12+`

### 手动验证前端
如果你想先单独确认前端依赖是否正常，可运行：
```bash
cd frontend
npm install
npm run build
```

修复后的仓库已经补齐 `frontend/package.json`、Vite 配置和基础 Vue 页面；之前 Windows 上执行到 `npm install` 报错的根因是仓库内没有 `frontend` 项目文件，导致启动脚本切换目录后无法找到可安装的前端依赖描述文件。

### 当前使用流程
1. 启动 `start.bat`
2. 在页面里先上传 `.zip` / `.cbz` / 单张图片
3. 选择翻译器、目标语言，并确认是否启用 GPU
4. 点击“开始翻译”
5. 等待页面中的进度条逐张推进，完成后可直接下载结果压缩包

### 如果之前已经创建过 `backend/venv`
由于 `manga-image-translator` 自身的 `pyproject.toml` 没有完整声明运行依赖，旧环境里可能会缺少 `python-dotenv` 等模块。更新代码后，建议：

```bat
cd backend
venv\Scripts\python -m pip install python-dotenv colorama
venv\Scripts\python -m pip install -r https://raw.githubusercontent.com/zyddnys/manga-image-translator/main/requirements.txt
venv\Scripts\python -m pip install git+https://github.com/zyddnys/manga-image-translator.git
```

如果希望最省事，也可以直接删除 `backend\venv` 后重新运行 `start.bat`。

## 常见问题
**1. 提示未找到 Python 或 Node.js**
请前往 [Python 官网](https://www.python.org/) 安装 Python 3.10+ (勾选 Add Python to PATH)，前往 [Node.js官网](https://nodejs.org/) 安装 Node。

**2. 显存溢出 (OOM)**
如果您在处理时后台报错 `CUDA out of memory`，说明显存不够（默认推荐 8GB 以上）。请在后台配置或环境变量中降低 batch_size 或选择显存占用更小的模型。
