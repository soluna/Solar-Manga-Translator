# 在 Windows 系统上部署与运行指南

由于您主要在 Mac 上进行编程，但计划在拥有 Nvidia GPU 的 Windows 电脑上运行深度学习模型以获得最佳加速效果，这是非常正确的选择（特别是在运行 YOLO、manga-ocr 和未来的 LaMa 等模型时）。

是的，**您需要将整个项目文件夹转移到 Windows 电脑上**。以下是完整的迁移和运行步骤：

## 第一步：从 Mac 转移项目到 Windows

1. 在您的 Mac 上，将 `<repo>` 这个文件夹打包（例如打成 `.zip`）。
2. 将压缩包发送/拷贝到您的 Windows 电脑上，并解压到一个您喜欢的目录（例如 `D:\manga-translator`）。

## 第二步：在 Windows 上准备必需的模型文件

在运行之前，您必须在 Windows 的项目目录中放入以下两个关键文件（如果尚未下载）：

1. **中文字体文件**:
   运行我为您准备的 Python 脚本，或者手动下载一款粗体中文字体（如 `NotoSansSC-Bold.otf`），并将其放在项目的 `fonts/` 目录下。
2. **文本检测模型**:
   前往开源社区（如 HuggingFace 或 GitHub 的 `comic-text-detector` Releases 页面），下载名为 `comic-text-detector.pt` 的模型权重文件（约 100MB+）。将其放入项目的 `models/` 目录下。

   *注：manga-ocr 的模型（约 300MB）会在第一次运行代码时，由 Transformers 库自动从 HuggingFace 下载并缓存到您的 Windows 用户目录下，无需手动干预。*

## 第三步：在 Windows 上一键运行 (利用 Nvidia GPU)

为了让您在 Windows 上省去配置 Python 虚拟环境和 CUDA 版本的烦恼，我已经为您编写了一个名为 **`run_windows.bat`** 的批处理脚本。

### 运行要求：
- 您的 Windows 电脑上必须已安装 **Python 3.9 ~ 3.12 之间的版本**（**强烈建议使用 Python 3.10 或 3.11**，暂不推荐最新的 3.13 版本，因为某些数据科学依赖包在 3.13 上尚未提供预编译的 Wheel 文件，会导致安装失败），并且在安装时勾选了“Add Python to PATH”（添加到系统环境变量）。
- 您的电脑拥有 **Nvidia 显卡**，并且已经安装了较新的显卡驱动。

### 操作步骤：
1. 打开您解压后的项目文件夹。
2. **双击运行 `run_windows.bat` 文件**。

### 这个脚本会自动为您做以下事情：
- 检查您的 Python 环境是否正常。
- 在项目目录中创建一个名为 `venv` 的独立的虚拟环境（不会污染您的系统 Python）。
- 自动激活虚拟环境，并为您下载安装 **支持 CUDA 11.8 (Nvidia GPU 加速) 的特定版本 PyTorch**（这一步可能需要下载 2GB+ 的文件，请保持网络畅通）。
- 自动安装 `requirements.txt` 中的其他所有依赖（Gradio, OpenCV, Ultralytics 等）。
- 启动项目，并在命令行打印出类似于 `Running on local URL:  http://127.0.0.1:7860` 的提示。

3. 打开 Windows 上的浏览器（如 Chrome / Edge），访问 `http://127.0.0.1:7860` 即可开始使用您的自动化漫画翻译平台！