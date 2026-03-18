import cv2
import numpy as np
from PIL import Image
from ultralytics import YOLO

class TextDetector:
    def __init__(self, model_path="models/comic-text-detector.pt"):
        """
        初始化漫画文本检测器
        需要预先下载 YOLOv8 训练好的 manga text detection 模型
        默认路径: models/comic-text-detector.pt
        """
        self.model_path = model_path
        # 实际运行前需要下载模型，如果是 None 则暂不加载，防止报错
        self.model = None 

    def load_model(self):
        if self.model is None:
            import os
            if not os.path.exists(self.model_path):
                print(f"警告: 找不到文本检测模型 {self.model_path}，请先下载！")
                print("通常可以从 HuggingFace 或 GitHub release 下载 comic-text-detector.pt")
                # Fallback to a base yolov8n just to prevent instant crash (though useless for manga)
                # self.model = YOLO('yolov8n.pt') 
            else:
                self.model = YOLO(self.model_path)
    
    def detect_text_boxes(self, pil_image):
        """
        检测图片中的对话气泡/文本框
        返回 Bounding Boxes 列表: [{"box": [x1, y1, x2, y2], "conf": 0.9}, ...]
        """
        self.load_model()
        if self.model is None:
            return []

        # YOLO expects BGR numpy array or PIL Image
        # 转换 PIL 为 BGR numpy
        cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        
        # 运行检测
        results = self.model(cv_image, conf=0.25) # conf_thresh = 0.25
        
        bboxes = []
        for result in results:
            boxes = result.boxes
            for box in boxes:
                # 获取坐标 xyxy
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                bboxes.append({
                    "box": [int(x1), int(y1), int(x2), int(y2)],
                    "conf": conf
                })
        
        # 按从上到下，从右到左排序 (日文阅读顺序)
        bboxes.sort(key=lambda b: (b['box'][1] // 50, -b['box'][0]))
        return bboxes

    def generate_mask(self, pil_image, bboxes, padding=5):
        """
        根据 Bounding Boxes 生成二值化 Mask，用于后续的 LaMa Inpainting
        """
        width, height = pil_image.size
        mask = np.zeros((height, width), dtype=np.uint8)
        
        for bbox_info in bboxes:
            x1, y1, x2, y2 = bbox_info['box']
            # 添加 padding，确保文本完全被遮盖
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(width, x2 + padding)
            y2 = min(height, y2 + padding)
            
            # 将检测到的文本区域填充为白色 (255)
            cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
            
        return Image.fromarray(mask, mode='L')
