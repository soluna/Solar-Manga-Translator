from PIL import Image

class MangaOCR:
    def __init__(self):
        """
        初始化 manga-ocr 模型
        首次运行会自动从 HuggingFace 下载模型 (约 300MB)
        """
        self.mocr = None

    def load_model(self):
        if self.mocr is None:
            print("正在加载 manga-ocr 模型... (首次运行可能需要下载)")
            from manga_ocr import MangaOcr
            self.mocr = MangaOcr()

    def crop_and_recognize(self, pil_image, bboxes):
        """
        裁剪所有的文本框，并对每个框执行日文 OCR
        返回包含文本内容的完整 Bounding Box 列表
        """
        self.load_model()
        
        results = []
        for bbox_info in bboxes:
            x1, y1, x2, y2 = bbox_info['box']
            # 裁剪气泡区域
            cropped_image = pil_image.crop((x1, y1, x2, y2))
            
            # 识别日文
            try:
                japanese_text = self.mocr(cropped_image)
                # print(f"OCR 识别结果: {japanese_text}")
            except Exception as e:
                print(f"OCR 失败: {e}")
                japanese_text = ""
                
            # 将识别结果合并到原有的信息中
            result_info = bbox_info.copy()
            result_info["original_text"] = japanese_text
            result_info["translated_text"] = "" # 留给 LLM 翻译填充
            results.append(result_info)
            
        return results
