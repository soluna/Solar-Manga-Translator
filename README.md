# Manga Auto-Translator

基于 LLM 和视觉模型的全自动漫画汉化工具。

## 核心模块
1. **文本检测**: 识别对话气泡边界 (Bounding Box)
2. **OCR**: 使用 `manga-ocr` 提取高精度日文
3. **LLM 翻译**: 结合整页语境的上下文翻译
4. **图像修补 (Inpainting)**: 擦除日文并保留网点纸背景
5. **排版 (Typesetting)**: 中文自动换行与居中渲染
6. **Web UI**: 基于 Gradio 的交互界面

## 安装
```bash
pip install -r requirements.txt
```

## 运行
```bash
python main.py
```
