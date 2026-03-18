import gradio as gr
import os

from modules.detector import TextDetector
from modules.ocr import MangaOCR
from modules.translator import LLMTranslator
from modules.inpainter import ImageInpainter
from modules.typesetter import Typesetter

# V2: 默认拉起基于 LaMa 的擦除模型
detector = TextDetector()
ocr = MangaOCR()
inpainter = ImageInpainter(model_type="lama") 
typesetter = Typesetter()

def process_single_image(image, api_key, model_choice, proxy_url):
    """V2 处理单张图片流水线"""
    if image is None:
        return None, "错误：请上传一张漫画图片。"
        
    log_messages = []
    def log(msg):
        print(msg)
        log_messages.append(msg)
        
    try:
        # V2: 引入带显式代理支持的通用 LLM Translator
        translator = LLMTranslator(api_key=api_key, model=model_choice, proxy_url=proxy_url)
        log(f"开始处理单张图片 (代理模式: {proxy_url if proxy_url else '关闭'})...")
        
        # 1. 文本检测
        log("1/5: 开始气泡检测...")
        bboxes = detector.detect_text_boxes(image)
        if not bboxes:
            log("未检测到气泡，跳过。")
            return image, "\n".join(log_messages)
            
        mask_image = detector.generate_mask(image, bboxes)
        
        # 2. 提取日文
        log("2/5: 开始提取日文 OCR...")
        bboxes = ocr.crop_and_recognize(image, bboxes)
        
        # 3. LLM 翻译
        log(f"3/5: 开始 {model_choice} 上下文翻译 ({len(bboxes)} 句台词)...")
        bboxes = translator.translate_batch(bboxes)
                
        # 4. 图像擦除 (LaMa)
        log("4/5: 开始 LaMa 神经网络无痕擦除...")
        clean_image = inpainter.inpaint(image, mask_image)
        
        # 5. 中文排版
        log("5/5: 开始渲染中文排版...")
        final_image = typesetter.render(clean_image, bboxes)
        
        log("🎉 漫画单页处理成功！")
        return final_image, "\n".join(log_messages)
        
    except Exception as e:
        error_msg = f"处理异常: {str(e)}"
        log(error_msg)
        return image, "\n".join(log_messages)

def create_ui():
    with gr.Blocks(title="Manga Auto-Translator V2", theme=gr.themes.Soft()) as app:
        gr.Markdown("# 漫画自动翻译工具 (Manga Translator V2 专业版)")
        gr.Markdown("🚀 **全新升级**: 引入 LaMa 生成式去文字算法 + LLM 直连代理防封锁。")
        
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### ⚙️ API 设置与网络代理")
                api_key = gr.Textbox(
                    label="Google/OpenAI API Key", 
                    type="password",
                    placeholder="例如: AIzaSy..."
                )
                proxy_url = gr.Textbox(
                    label="HTTP 网络代理 (国内必填，解决翻译失败！)", 
                    placeholder="例如: http://127.0.0.1:7890",
                    info="如果您在电脑上开着 Clash / v2ray，请填入对应的本地代理地址（通常是 7890 端口）。留空则直连。"
                )
                model_choice = gr.Dropdown(
                    choices=["gemini-1.5-pro", "gemini-1.5-flash", "gpt-4o", "claude-3-5-sonnet-20240620"],
                    label="选择大模型",
                    value="gemini-1.5-pro"
                )
                
                gr.Markdown("---")
                input_image = gr.Image(type="pil", label="上传日文漫画原图")
                process_btn = gr.Button("🎨 提取、擦除并翻译", variant="primary", size="lg")
                
            with gr.Column(scale=1):
                output_image = gr.Image(type="pil", label="LaMa 无痕汉化成品", interactive=False)
                output_log = gr.Textbox(label="处理实时日志 (Console Log)", lines=15, max_lines=25)
                
        process_btn.click(
            fn=process_single_image,
            inputs=[input_image, api_key, model_choice, proxy_url],
            outputs=[output_image, output_log]
        )
                
    return app

if __name__ == "__main__":
    app = create_ui()
    app.launch(server_name="0.0.0.0", server_port=7860, share=False)
