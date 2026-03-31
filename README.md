# Manga Auto-Translator WebUI

基于 manga-image-translator 封装的本地全流程极速翻译画廊。提供极佳的大文件（zip/cbz）自动翻译体验，支持“流式”出图、边译边看，并开放完整的高级配置。充分利用本地独立显卡进行高性能计算加速。本系统为 **Windows + CUDA** 量身打造。

## 核心模块
1. **文本检测**: 识别对话气泡边界 (Bounding Box)
2. **OCR**: 提取高精度日文
3. **翻译**: 可选使用 Google Translate, DeepL, 网易有道，或结合整页语境上下文的 GPT-4 等 LLM 翻译
4. **图像修补 (Inpainting)**: 擦除日文并保留网点纸背景
5. **排版 (Typesetting)**: 中文自动换行与居中渲染
6. **Web UI**: 基于 Vue3 和 Tailwind CSS 构建的现代化画廊界面，支持拖拽压缩包流式处理

## 安装与启动 (一键运行)
为了方便在 Windows 上使用，我们提供了一键启动脚本 `start.bat`。

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
