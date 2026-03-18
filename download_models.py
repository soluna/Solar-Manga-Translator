import urllib.request
import os

print("准备下载测试用中文字体...")
font_dir = "fonts"
os.makedirs(font_dir, exist_ok=True)
font_path = os.path.join(font_dir, "NotoSansSC-Bold.otf")

if not os.path.exists(font_path):
    print("正在下载思源黑体 (Noto Sans SC Bold)... (约10MB)")
    try:
        url = "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Bold.otf"
        urllib.request.urlretrieve(url, font_path)
        print("字体下载成功！")
    except Exception as e:
        print(f"字体下载失败: {e}。请手动下载中文字体并放入 fonts/ 目录下。")
else:
    print("字体文件已存在。")

print("\n关于 YOLO 模型 (comic-text-detector.pt):")
print("由于模型文件较大且常托管在 GitHub Releases，建议您手动下载。")
print("下载地址参考: https://github.com/manga-download/comic-text-detector/releases")
print("请将下载的 .pt 模型放入 models/ 目录中并命名为 comic-text-detector.pt")
