# 自动漫画汉化工具 (Manga Translator WebUI) 设计规范

## 1. 项目概述
基于开源引擎 `manga-image-translator` 打造的本地 Web 服务。提供极佳的大文件（zip/cbz）自动翻译体验，支持“流式”出图、边译边看，并开放完整的高级配置。充分利用本地独立显卡进行高性能计算加速。本系统为 **Windows + CUDA** 量身打造，采用跨平台友好的部署和开源托管策略。

## 2. 核心交互流程
1. **上传区**: 用户可以通过拖拽上传单张图片或整个 `.zip` / `.cbz` 压缩包。
2. **高级配置面板**: 上传前或处理前，用户可以调整底层所有的配置项（如：文本检测模型、翻译器类型及 API Key、字体样式、排版策略）。
3. **实时处理页**:
   - 前端通过 WebSocket 与后端建立长连接。
   - 后端解压压缩包后，逐张调用翻译引擎。
   - 每翻译完一张，立即通过 WebSocket 将翻译后的图片推送到前端展示。
   - 前端形成一个“画廊/阅读器”界面，图片随着翻译进度一张张出现，用户可以立刻开始阅读。
4. **结果导出**: 整体翻译完成后，提供“一键下载全本压缩包”的按钮。

## 3. 架构设计
### 3.1 前端 (WebUI)
- 框架：推荐使用轻量级框架（如 Vue 3 或 React）结合 Tailwind CSS 进行快速构建。
- 组件：
  - 文件上传组件（支持拖放，大文件分片或表单直传）。
  - 高级配置表单（动态绑定后端的参数配置）。
  - WebSocket 进度条与状态监视器。
  - 瀑布流/分页式的漫画阅读器组件（用于展示已翻译的图片）。

### 3.2 后端 (Python/FastAPI)
由于 `manga-image-translator` 是 Python 项目，后端直接使用 Python 开发。
- **环境要求与 GPU 加速**：
  - 深度依赖 **PyTorch** 及 **CUDA** 以调用本地 NVIDIA GPU（如 RTX 4060Ti）。
  - 处理流程中的 **OCR 文本检测** 和 **Inpainting (涂抹去字)** 环节极为消耗算力，需确保安装支持 CUDA 的 PyTorch 才能实现最佳速度。
  - 需要提供适用于 Windows 和 Linux 系统的 `requirements.txt`。并在项目 Readme 中补充 Windows+CUDA+PyTorch 的一键安装指令指引。
- 框架：使用 **FastAPI**，天然支持异步 (asyncio) 和 WebSocket，非常适合这种长时间运行的推理任务。
- 核心服务调用：将 `manga-image-translator` 的处理逻辑封装为一个可实例化的类或函数。
- 处理流程：
  1. 接收文件并存储到临时目录。
  2. 如果是压缩包，解压到工作目录。
  3. 创建后台异步任务（Task），开始遍历图片。
  4. 遍历时，同步/异步调用翻译引擎的 `translate()` 接口（此阶段由 GPU 执行核心模型推理）。
  5. 每翻译完成一张，将结果通过 WebSocket 推送到对应客户端。

## 4. 关键数据流 (WebSocket 协议)
- `{"event": "start", "total_pages": 45}`: 开始处理压缩包通知。
- `{"event": "progress", "current": 1, "total": 45, "image_url": "/output/page_01_translated.jpg"}`: 每张图片处理完成时的推送。
- `{"event": "error", "message": "API Limit Reached"}`: 错误反馈。
- `{"event": "completed", "download_url": "/download/comic_translated.zip"}`: 全部完成通知。

## 5. 错误处理与容灾
- **前端重连**: WebSocket 断开时，前端支持自动重连，后端需保持会话状态（可以通过 session_id 恢复进度读取）。
- **翻译器异常**: 遇到单个翻译 API 超时或报错，支持跳过该图（标记为失败图）并继续下一张，或者暂停任务等待用户重新配置 API key 继续。
- **显存保护 (OOM)**: 在调用 GPU 推理时捕获 OutOfMemory 错误，提供释放显存重试或自动降级策略。
- **文件清理**: 定时清理过期的上传文件和翻译结果文件。

## 6. 测试与验证
1. 测试单张图片上传、高级参数调整（如切换 DeepL 翻译）是否生效。
2. 验证模型是否成功挂载到 CUDA 设备，利用 RTX 4060Ti 加速 OCR 和 Inpainting 过程。
3. 测试上传包含 10 张图片的 `.cbz`，验证 WebSocket 推送是否流畅，前端显示是否“连载更新”。
4. 验证全部翻译完成后，打包下载的 `.zip` 结构和图片质量。