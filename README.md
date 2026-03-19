# Manga Auto-Translator WebUI

基于 manga-image-translator 封装的本地全流程极速翻译画廊。提供极佳的大文件（zip/cbz）自动翻译体验，支持“流式”出图、边译边看，并开放完整的高级配置。充分利用本地独立显卡进行高性能计算加速。本系统为 **Windows + CUDA** 量身打造。

## 核心模块
1. **文本检测**: 识别对话气泡边界 (Bounding Box)
2. **OCR**: 提取高精度日文
3. **翻译**: 可选使用 Google Translate, DeepL, 网易有道，或结合整页语境上下文的 GPT-4 等 LLM 翻译
4. **图像修补 (Inpainting)**: 擦除日文并保留网点纸背景
5. **排版 (Typesetting)**: 中文自动换行与居中渲染
6. **Web UI**: 基于 Vue3 和 Tailwind CSS 构建的现代化画廊界面，支持拖拽压缩包流式处理

## 环境准备 (Windows + RTX GPU)
为了利用显卡加速，必须先安装支持 CUDA 的 PyTorch：
```bash
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```
然后再安装后端依赖和开源引擎：
```bash
cd manga-translator/backend
pip install -r requirements.txt
pip install git+https://github.com/zyddnys/manga-image-translator.git
```

前端依赖安装：
```bash
cd manga-translator/frontend
npm install
```

## 运行
后端 (FastAPI/WebSocket 服务):
```bash
cd manga-translator/backend
uvicorn main:app --host 0.0.0.0 --port 8000
```

前端 (Vue3/Vite):
```bash
cd manga-translator/frontend
npm run dev
```