# Solar-Manga-Translator

Solar-Manga-Translator 是一个本地优先的漫画图片翻译工作台。它把上传、OCR、翻译、修图、嵌字、人工校对和导出整理在同一个 Web UI / 桌面应用流程里，适合中文用户在自己的电脑上处理合法拥有或获授权处理的漫画、图像文本和条漫素材。

项目的目标不是把翻译过程做成不可见的黑盒，而是给你一个可以检查、可以调整、可以复现的本地工具：先让程序完成批量识别和初稿生成，再由你在校对界面里逐页修正文本、排版和样式。

## 界面预览

以下截图使用项目内生成的合成演示素材，不包含真实漫画页面或版权内容。

![上传首页](docs/screenshots/upload-home.png)

![项目页面列表](docs/screenshots/project-pages.png)

![审校工作台](docs/screenshots/review-workspace.png)

![历史项目](docs/screenshots/history-projects.png)

## 基本使用流程

1. 启动应用：Windows 用户运行 `start.bat`，macOS 用户运行 `start.mac.sh`，Linux 用户运行 `start.sh`。
2. 上传素材：在首页拖入单张图片、图片文件夹、`.zip` 或 `.cbz`。
3. 配置服务：在设置面板中选择翻译服务、目标语言、字体映射和图像处理方式；需要密钥的服务会保存在本地配置中，前端只看到脱敏状态。
4. 执行翻译：让后端依次完成 OCR、翻译、擦字、修补和嵌字流程；长任务会在顶部状态区显示进度。
5. 人工审校：进入审校工作台，逐页调整文本框、译文、字号、字体、排版方向和对照视图。
6. 导出结果：确认后导出图片或归档文件；历史项目可以从本地项目列表恢复继续编辑。

## 项目状态

- 当前源码仓库已经按公开开源发布标准做过清理。
- 许可证为 GPL-3.0-only。
- 桌面安装包仍处于实验阶段，正式分发前需要在干净 Windows 环境重新构建、审计和生成校验信息。
- 实际翻译工作建议使用 Windows + NVIDIA GPU + CUDA 兼容 PyTorch。macOS 和 Linux 更适合作为开发、调试和轻量测试环境。

## 主要功能

- 支持导入单张图片、文件夹、`.zip` 和 `.cbz` 漫画包。
- 支持 OCR、翻译、文字擦除、图像修补和自动嵌字流程。
- 支持长任务进度流式更新，便于观察每一步处理状态。
- 提供浏览器校对工作区，可编辑文本区域、译文、布局和基础样式。
- 支持本机字体和用户自行放入的本地字体，不在仓库内捆绑字体文件。
- 支持导出处理后的图片和归档结果。
- 默认使用本机回环地址启动服务，并通过临时 API Token 保护会修改本地状态的接口。

## 仓库不包含什么

本仓库不会、也不应该包含以下内容：

- 漫画原图、翻译后的漫画成品、测试用版权图页。
- 商业字体、字体二进制文件或私有字体包。
- 模型权重、模型缓存、运行时下载缓存。
- API Key、`.env`、本地日志、个人项目数据、个人机器路径。
- 打包产生的安装器、临时上传目录、输出目录和其他本地运行产物。

请只处理你拥有权利或获得授权的图片内容。提交 Pull Request 时，也不要加入版权漫画页面、翻译成品截图、字体文件、模型权重或任何凭据。

## 目录结构

- `backend/`：FastAPI 后端、上传与归档校验、本地路径管理、设置存储、翻译引擎集成。
- `frontend/`：Vue 3 + Vite 前端界面，以及 Playwright 冒烟测试脚本。
- `desktop/`：Electron 桌面壳和 Windows 打包脚本。
- `scripts/`：本地辅助脚本和合成测试素材生成脚本。
- `docs/`：发布检查清单、开源发布审计记录和桌面打包说明。

## 环境要求

- Git
- Python 3.10 或 3.11
- Node.js 18 或更新版本
- Windows 用户建议准备 NVIDIA GPU 和 CUDA 兼容的 PyTorch 环境

首次安装可能会下载较大的 Python 包和模型文件，具体取决于你启用的 OCR、翻译和修图后端。

## 快速开始

### Windows

```bat
start.bat
```

脚本会创建 `backend/venv`，安装依赖，准备固定版本的核心翻译引擎，安装前端依赖，启动本地后端和前端，并打开浏览器窗口。

### macOS

```bash
chmod +x start.mac.sh
bash ./start.mac.sh
```

macOS 脚本会使用 `backend/.venv-mac`，避免影响 Windows 侧常用的 `backend/venv`。

### Linux

```bash
chmod +x start.sh
./start.sh
```

默认启动脚本会把本地服务绑定到 `127.0.0.1`，并为浏览器请求生成临时 API Token。

## 手动开发启动

启动后端：

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python install_deps.py
python -m pip install -r requirements.txt
export APP_API_TOKEN="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
printf 'APP_API_TOKEN=%s\n' "$APP_API_TOKEN"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

另开一个终端启动前端，把下面的 `<same-token>` 替换成后端终端打印出的 Token：

```bash
cd frontend
npm ci
VITE_API_BASE_URL=http://127.0.0.1:8000 VITE_API_TOKEN="<same-token>" npm run dev -- --host 127.0.0.1
```

桌面开发：

```bash
cd desktop
npm ci
npm run dev
```

## 安全默认值

- 除非设置 `APP_ENABLE_API_DOCS=1`，否则后端不会暴露 Swagger/OpenAPI 文档页面。
- 本地服务默认只监听回环地址。
- `APP_API_TOKEN` 或 `MANGA_TRANSLATOR_API_TOKEN` 会保护修改本地状态的 API 和 WebSocket 会话。
- 保存的服务商密钥只保留在服务端，本地 API 响应会做脱敏处理。
- 上传和归档处理会校验大小、路径、归档结构、文件类型和图片完整性。
- 桌面打包使用 allowlist，排除本地字体、模型、缓存、输出、示例、临时上传和核心引擎 `.git` 目录。

安全问题报告方式见 `SECURITY.md`。

## 测试

后端测试：

```bash
python -m unittest discover backend/tests -v
```

前端构建：

```bash
cd frontend
npm ci
npm run build
```

浏览器冒烟测试：

```bash
cd frontend
npm run test:canvas-preview
npm run test:review-workspace
npm run test:v2-workspace
```

桌面脚本语法检查：

```bash
node --check desktop/main.mjs
node --check desktop/preload.mjs
node --check desktop/scripts/dev.mjs
node --check desktop/scripts/stage-runtime.mjs
node --check desktop/scripts/package-win.mjs
```

准备发布候选版本时，还应执行 `docs/release-checklist.md` 中列出的依赖、内容和打包检查。

## Windows 桌面打包

Electron 桌面包仍是实验功能。请在干净 Windows 环境构建：

```powershell
cd desktop
npm ci
npm run dist:win
```

发布安装包前至少需要完成：

- 从干净 Python runtime 重新构建。
- 检查 `desktop/resources-staging/release-manifest.json`。
- 扫描暂存目录和最终安装包，确认没有密钥、个人路径、字体、版权媒体、模型权重和异常大文件。
- 生成 SBOM 或等价的许可证/依赖报告。
- 发布 SHA-256 校验和。
- 决定是否需要代码签名。

最近一次本地审计仍提示机器学习依赖栈中的 Torch 相关安全公告没有可由 `pip-audit` 自动建议的修复版本。正式分发桌面安装包前，请对干净 release runtime 重新审计。

## 贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读 `CONTRIBUTING.md` 和 `CODE_OF_CONDUCT.md`。

提交 Pull Request 时请说明：

- 改了什么，为什么要改。
- 运行过哪些测试。
- 是否涉及迁移、兼容性或打包影响。
- 是否确认没有加入私有数据、版权媒体、字体二进制、模型权重或凭据。

## 来源、许可证与致谢

Solar-Manga-Translator 以 GPL-3.0-only 发布，详见 `LICENSE`。

本项目集成并固定使用 `manga-image-translator` 作为核心图片翻译引擎，并在本仓库中维护必要的运行时补丁。相关上游来源、许可证和第三方声明见 `NOTICE` 与 `THIRD_PARTY_NOTICES.md`。

核心引擎版本配置见 `backend/upstream.json`，依赖快照见 `backend/requirements-upstream.txt`，运行时补丁见 `backend/patch_pydensecrf.py` 与 `backend/patched_*.py`。正式发布构建不要把固定 commit 替换成未固定的分支。
