import cv2
import numpy as np
from PIL import Image

class ImageInpainter:
    def __init__(self, model_type="lama"):
        """
        初始化图像修复模块
        V2 架构：默认使用基于深度学习的 LaMa 模型，它在擦除复杂漫画背景（网点纸/线条）时效果远超 OpenCV。
        """
        self.model_type = model_type
        self.lama_model = None
        
    def load_lama(self):
        if self.lama_model is None:
            print("首次运行 LaMa Inpainting，正在加载模型并传送至 GPU... (首次可能需下载约100MB)")
            try:
                from simple_lama_inpainting import SimpleLama
                self.lama_model = SimpleLama()
                print("✅ LaMa 擦除模型加载成功！")
            except Exception as e:
                print(f"❌ LaMa 模型加载失败: {e}，将回退至 OpenCV 修补")
                self.model_type = "opencv"
        
    def inpaint(self, pil_image, pil_mask):
        """
        根据提供的 Mask 擦除原图上的文字
        :param pil_image: 原始漫画页 (PIL Image)
        :param pil_mask: 包含文本区域的二值化遮罩图 (PIL Image, 'L' mode)
        :return: 擦除文字后的底图 (PIL Image)
        """
        # 1. 转换 Mask 格式: PIL 'L' mode to numpy
        mask_cv = np.array(pil_mask)
        if len(mask_cv.shape) == 3:
            mask_cv = cv2.cvtColor(mask_cv, cv2.COLOR_BGR2GRAY)
            
        # 2. 激进膨胀 Mask (V2 优化)
        # 将 Mask 膨胀 7x7 甚至 9x9 像素，确保汉字边缘的抗锯齿和黑边能被完全包裹进去擦除
        kernel = np.ones((7, 7), np.uint8)
        mask_dilated = cv2.dilate(mask_cv, kernel, iterations=1)
        
        if self.model_type == "lama":
            self.load_lama()
            
        # 3. 执行修补算法
        if self.model_type == "lama" and self.lama_model is not None:
            # LaMa 接收 PIL Image 和 PIL Mask
            dilated_pil_mask = Image.fromarray(mask_dilated, mode='L')
            
            # LaMa 内部要求 Mask 的白色区域 (255) 为需要擦除的地方
            try:
                inpainted_pil = self.lama_model(pil_image, dilated_pil_mask)
                return inpainted_pil
            except Exception as e:
                print(f"LaMa 擦除过程中出错: {e}，回退至 OpenCV")
                self.model_type = "opencv" # 失败则走后备方案
                
        # Fallback 方案：OpenCV
        if self.model_type == "opencv":
            img_cv = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            # 使用 NS (Navier-Stokes) 算法，对某些网点纸纹理保留更好
            inpainted_cv = cv2.inpaint(img_cv, mask_dilated, inpaintRadius=3, flags=cv2.INPAINT_NS)
            inpainted_rgb = cv2.cvtColor(inpainted_cv, cv2.COLOR_BGR2RGB)
            return Image.fromarray(inpainted_rgb)

    def draw_mask_on_image(self, pil_image, pil_mask):
        """用于 UI 调试：将半透明红色的 Mask 叠加在原图上显示检测区域"""
        img_np = np.array(pil_image.convert("RGBA"))
        mask_np = np.array(pil_mask)
        
        red_layer = np.zeros_like(img_np)
        red_layer[:, :, 0] = 255 # R
        red_layer[:, :, 3] = 128 # Alpha = 50%
        
        condition = mask_np > 0
        img_np[condition] = red_layer[condition]
        
        return Image.fromarray(img_np, 'RGBA').convert("RGB")
