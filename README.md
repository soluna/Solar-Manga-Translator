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

### Linux/macOS
打开终端，赋予可执行权限后运行：
```bash
chmod +x start.sh
./start.sh
```

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

## 常见问题
**1. 提示未找到 Python 或 Node.js**
请前往 [Python 官网](https://www.python.org/) 安装 Python 3.10+ (勾选 Add Python to PATH)，前往 [Node.js官网](https://nodejs.org/) 安装 Node。

**2. 显存溢出 (OOM)**
如果您在处理时后台报错 `CUDA out of memory`，说明显存不够（默认推荐 8GB 以上）。请在后台配置或环境变量中降低 batch_size 或选择显存占用更小的模型。
