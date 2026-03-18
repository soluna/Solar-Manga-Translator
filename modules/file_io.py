import os
import zipfile
import tempfile
from PIL import Image

class FileProcessor:
    def __init__(self, output_dir="output"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
    def extract_archive(self, archive_path):
        """解压 .zip 或 .cbz 文件到临时目录"""
        temp_dir = tempfile.mkdtemp(prefix="manga_")
        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        return temp_dir

    def load_images_from_dir(self, directory):
        """从目录加载所有支持的图片"""
        valid_exts = ('.png', '.jpg', '.jpeg', '.webp')
        images = []
        for root, _, files in os.walk(directory):
            for file in sorted(files):
                if file.lower().endswith(valid_exts):
                    images.append(os.path.join(root, file))
        return images
        
    def save_image(self, pil_image, filename):
        """保存处理后的图片"""
        out_path = os.path.join(self.output_dir, filename)
        pil_image.save(out_path)
        return out_path
