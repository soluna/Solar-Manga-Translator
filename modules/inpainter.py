import cv2
import numpy as np
from PIL import Image

class ImageInpainter:
    def __init__(self, model_type="opencv"):
        """
        初始化图像修复模块
        目前出于无需复杂的本地编译依赖，优先提供 OpenCV 基于 Telea 的快速修补算法。
        您可以随时将它替换为更强大的深度学习模型如 LaMa (Large Mask Inpainting)。
        """
        self.model_type = model_type
        
    def inpaint(self, pil_image, pil_mask):
        """
        根据提供的 Mask 擦除原图上的文字
        :param pil_image: 原始漫画页 (PIL Image)
        :param pil_mask: 包含文本区域的二值化遮罩图 (PIL Image, 'L' mode)
        :return: 擦除文字后的底图 (PIL Image)
        """
        # 1. 转换图像格式: PIL to OpenCV (BGR)
        img_cv = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        
        # 2. 转换 Mask 格式: PIL 'L' mode to numpy
        mask_cv = np.array(pil_mask)
        # 确保 Mask 是单通道 uint8
        if len(mask_cv.shape) == 3:
            mask_cv = cv2.cvtColor(mask_cv, cv2.COLOR_BGR2GRAY)
            
        # 3. 膨胀 Mask 以确保文本边缘也能被干净擦除
        kernel = np.ones((5, 5), np.uint8)
        mask_dilated = cv2.dilate(mask_cv, kernel, iterations=1)
        
        # 4. 执行修补算法
        if self.model_type == "opencv":
            # 使用 cv2.INPAINT_TELEA 算法 (快速，但对于复杂的网点纸背景可能略显模糊)
            # 使用 cv2.INPAINT_NS (Navier-Stokes) 对于某些情况纹理保留更好
            inpainted_cv = cv2.inpaint(img_cv, mask_dilated, inpaintRadius=5, flags=cv2.INPAINT_TELEA)
            
        elif self.model_type == "lama":
            # [TODO] 接入 LaMa 模型推理
            # LaMa (Resolution-robust Large Mask Inpainting) 需要加载额外的 .pth 模型
            # 这是一个占位符，如果安装了 simple-lama-inpainting 库，可以在这里调用：
            # from simple_lama_inpainting import SimpleLama
            # lama = SimpleLama()
            # inpainted_pil = lama(pil_image, Image.fromarray(mask_dilated))
            # return inpainted_pil
            print("LaMa Inpainting 暂未启用，回退至 OpenCV Telea 算法...")
            inpainted_cv = cv2.inpaint(img_cv, mask_dilated, 5, cv2.INPAINT_TELEA)
        else:
            raise ValueError(f"未知的 Inpaint 模型类型: {self.model_type}")
            
        # 5. 转换回 PIL Image 格式
        inpainted_rgb = cv2.cvtColor(inpainted_cv, cv2.COLOR_BGR2RGB)
        return Image.fromarray(inpainted_rgb)

    def draw_mask_on_image(self, pil_image, pil_mask):
        """
        用于 UI 调试：将半透明红色的 Mask 叠加在原图上显示检测区域
        """
        img_np = np.array(pil_image.convert("RGBA"))
        mask_np = np.array(pil_mask)
        
        # 创建一个红色半透明的覆盖层
        red_layer = np.zeros_like(img_np)
        red_layer[:, :, 0] = 255 # R
        red_layer[:, :, 3] = 128 # Alpha = 50%
        
        # 只有 Mask 的区域应用红色覆盖层
        condition = mask_np > 0
        img_np[condition] = red_layer[condition]
        
        return Image.fromarray(img_np, 'RGBA').convert("RGB")
