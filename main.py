import gradio as gr
import os
import io
from PIL import Image

# 导入核心模块
from modules.detector import TextDetector
from modules.ocr import MangaOCR
from modules.translator import LLMTranslator
from modules.inpainter import ImageInpainter
from modules.typesetter import Typesetter

# 初始化全局模块实例 (避免每次请求都重新加载庞大的深度学习模型)
detector = TextDetector()
ocr = MangaOCR()
inpainter = ImageInpainter(model_type="opencv")  # 默认使用轻量级 OpenCV 修补
typesetter = Typesetter()

def process_image(image, api_key, model_choice):
    if image is None:
        return None, "错误：请上传一张漫画图片。"
        
    log_messages = []
    def log(msg):
        print(msg)
        log_messages.append(msg)
        
    try:
        # 1. 文本检测 (Text Detection)
        log("开始步骤 1: 文本框/气泡检测 (YOLO)")
        bboxes = detector.detect_text_boxes(image)
        log(f"检测到 {len(bboxes)} 个潜在文本区域。")
        
        # UI 调试图：生成半透明红色遮罩图以展示检测区域
        mask_image = detector.generate_mask(image, bboxes)
        # debug_overlay = inpainter.draw_mask_on_image(image, mask_image)
        
        # 2. 提取日文 (Manga OCR)
        log("开始步骤 2: 裁剪区域并执行日文 OCR (manga-ocr)")
        bboxes = ocr.crop_and_recognize(image, bboxes)
        for i, b in enumerate(bboxes):
            jp_txt = b.get('original_text', '')
            if jp_txt:
                log(f"  框 {i+1}: {jp_txt}")
                
        # 3. LLM 翻译 (Translation)
        log(f"开始步骤 3: 调用 {model_choice} 进行上下文翻译")
        translator = LLMTranslator(api_key=api_key, model=model_choice)
        bboxes = translator.translate_batch(bboxes)
        for i, b in enumerate(bboxes):
            zh_txt = b.get('translated_text', '')
            if zh_txt:
                log(f"  译文 {i+1}: {zh_txt}")
                
        # 4. 图像擦除 (Inpainting)
        log("开始步骤 4: 擦除原始日文字符 (Inpainting)")
        clean_image = inpainter.inpaint(image, mask_image)
        
        # 5. 中文排版 (Typesetting)
        log("开始步骤 5: 在干净底图上进行中文自动折行排版与居中渲染")
        final_image = typesetter.render(clean_image, bboxes)
        
        log("🎉 处理完成！")
        return final_image, "\n".join(log_messages)
        
    except Exception as e:
        error_msg = f"处理过程中发生异常: {str(e)}"
        log(error_msg)
        return image, "\n".join(log_messages)

def create_ui():
    with gr.Blocks(title="Manga Auto-Translator", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 漫画自动翻译工具 (Manga Auto-Translator)")
        gr.Markdown("基于 LLM 与计算机视觉的自动化漫画汉化流水线。此版本集成了 `YOLO` 检测、`manga-ocr` 识别、OpenCV 修补以及 PIL 自动排版引擎。")
        
        with gr.Row():
            with gr.Column(scale=1):
                api_key = gr.Textbox(
                    label="LLM API Key (如不填将尝试从环境变量 OPENAI_API_KEY 读取)", 
                    type="password",
                    placeholder="sk-..."
                )
                model_choice = gr.Dropdown(
                    choices=["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "claude-3-5-sonnet-20240620", "deepseek-chat"],
                    label="选择大语言模型",
                    value="gpt-4o"
                )
                
                input_image = gr.Image(type="pil", label="1. 上传需翻译的日文漫画页")
                process_btn = gr.Button("🚀 提取并翻译", variant="primary", size="lg")
                
            with gr.Column(scale=1):
                output_image = gr.Image(type="pil", label="2. 汉化成品预览", interactive=False)
                output_log = gr.Textbox(label="处理日志 (Console Log)", lines=12, max_lines=20)
                
        process_btn.click(
            fn=process_image,
            inputs=[input_image, api_key, model_choice],
            outputs=[output_image, output_log]
        )
        
        gr.Markdown("---")
        gr.Markdown("### ⚠️ 首次运行注意事项")
        gr.Markdown("1. **YOLO 权重**: 需要将名为 `comic-text-detector.pt` 的模型放入 `models/` 目录中。如果没有，系统将提示找不到模型。")
        gr.Markdown("2. **manga-ocr 权重**: 首次执行 OCR 时，会自动从 HuggingFace 下载约 300MB 的 transformer 模型。")
        gr.Markdown("3. **中文字体**: 排版模块默认需要一款粗体中文字体（如思源黑体），建议在 `fonts/` 目录下提供 `NotoSansSC-Bold.otf`。")
        
    return app

if __name__ == "__main__":
    app = create_ui()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
