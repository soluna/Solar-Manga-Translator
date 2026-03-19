# Manga Translator WebUI

基于 manga-image-translator 封装的本地全流程极速翻译画廊。

## 环境准备 (Windows + RTX GPU)
为了利用显卡加速，必须先安装支持 CUDA 的 PyTorch：
```bash
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```
然后再安装本项目依赖和开源引擎：
```bash
cd backend
pip install -r requirements.txt
pip install git+https://github.com/zyddnys/manga-image-translator.git
```