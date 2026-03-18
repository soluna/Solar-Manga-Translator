import math
from PIL import Image, ImageDraw, ImageFont

class Typesetter:
    def __init__(self, font_path="fonts/NotoSansSC-Bold.otf", default_color=(0, 0, 0)):
        """
        初始化漫画文字排版器
        默认使用黑色字体。
        需要提供一个支持中文的字体文件路径。
        """
        self.font_path = font_path
        self.default_color = default_color
        
    def load_font(self, font_size):
        try:
            return ImageFont.truetype(self.font_path, font_size)
        except Exception:
            # 如果找不到字体，尝试系统默认
            print(f"警告：找不到字体文件 {self.font_path}，使用系统默认字体")
            return ImageFont.load_default()

    def _auto_wrap_text(self, text, font, max_width, draw):
        """
        根据给定的最大宽度自动折行中文字符串
        """
        lines = []
        current_line = ""
        
        for char in text:
            test_line = current_line + char
            # 获取文本渲染宽度
            # draw.textbbox((0, 0), test_line, font=font) 返回 (left, top, right, bottom)
            bbox = draw.textbbox((0, 0), test_line, font=font)
            width = bbox[2] - bbox[0]
            
            if width <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = char
                
        if current_line:
            lines.append(current_line)
            
        return lines

    def _calculate_best_font_size(self, text, box_width, box_height, draw):
        """
        二分查找法：计算适合 Bounding Box 的最大字体大小，使得多行文字能完整放入框内。
        """
        min_size = 10
        max_size = min(box_width, box_height) // 2  # 字体最大不会超过框的一半高
        best_size = min_size
        best_lines = []
        
        while min_size <= max_size:
            mid_size = (min_size + max_size) // 2
            font = self.load_font(mid_size)
            
            # 使用略小于框宽度的宽度进行折行 (比如 0.8 倍)
            # 以避免文字贴紧气泡边缘
            lines = self._auto_wrap_text(text, font, int(box_width * 0.8), draw)
            
            # 计算总高度
            total_height = 0
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                total_height += (bbox[3] - bbox[1]) + 2 # 加上行距
                
            if total_height <= int(box_height * 0.8):
                # 如果放得下，尝试更大的字体
                best_size = mid_size
                best_lines = lines
                min_size = mid_size + 1
            else:
                # 放不下，缩小字体
                max_size = mid_size - 1
                
        return best_size, best_lines

    def render(self, pil_image, bboxes):
        """
        在干净的底图上，将翻译好的中文文本重新排版渲染到对应的气泡 Bounding Box 内。
        :param pil_image: 已经擦除原文的干净底图 (PIL Image)
        :param bboxes: 包含 'box', 'translated_text' 等键的列表
        :return: 渲染好中文台词的最终漫画图片
        """
        # 创建可绘制的图像副本
        result_img = pil_image.copy()
        draw = ImageDraw.Draw(result_img)
        
        for bbox_info in bboxes:
            x1, y1, x2, y2 = bbox_info['box']
            translated_text = bbox_info.get("translated_text", "").strip()
            
            if not translated_text:
                continue
                
            box_width = x2 - x1
            box_height = y2 - y1
            
            # 计算最佳字号和自动折行
            best_size, lines = self._calculate_best_font_size(translated_text, box_width, box_height, draw)
            font = self.load_font(best_size)
            
            # 计算整个文本块的总高度，用于垂直居中
            total_text_height = sum([draw.textbbox((0,0), line, font=font)[3] - draw.textbbox((0,0), line, font=font)[1] for line in lines])
            # 添加行距 (假设行距为2像素)
            total_text_height += (len(lines) - 1) * 2
            
            # 垂直居中起始 Y 坐标
            start_y = y1 + (box_height - total_text_height) // 2
            
            # 逐行居中渲染
            current_y = start_y
            for line in lines:
                bbox = draw.textbbox((0,0), line, font=font)
                line_width = bbox[2] - bbox[0]
                line_height = bbox[3] - bbox[1]
                
                # 水平居中 X 坐标
                start_x = x1 + (box_width - line_width) // 2
                
                # 绘制文字边缘描边 (白色) - 防止背景太暗看不清黑字
                stroke_width = max(1, best_size // 15)
                draw.text((start_x, current_y), line, font=font, fill=self.default_color, 
                          stroke_width=stroke_width, stroke_fill=(255, 255, 255))
                
                # 更新下一行的 Y 坐标
                current_y += line_height + 2
                
        return result_img
