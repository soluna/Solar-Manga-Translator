# 自动漫画汉化工具 (Manga Translator WebUI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 `manga-image-translator` 构建一个支持 WebSocket 流式推送、完整高级配置、以及 GPU 加速支持的跨平台（重点兼顾 Windows+CUDA）漫画汉化 WebUI 系统。

**Architecture:** 前端采用 Vue 3 构建极简响应式界面，通过 WebSocket 和后端保持长连接。后端基于 FastAPI 和 asyncio，接收前端指令和文件后，解压 cbz/zip 压缩包，放入后台异步队列，利用本地 CUDA 设备（如 RTX 4060Ti）调用 `manga-image-translator` 的核心引擎逐张进行 OCR 和图片修复（Inpainting），处理完一张立刻将 URL 和进度推给前端。

**Tech Stack:**
- 前端：Vue 3 (Composition API), Vite, Tailwind CSS
- 后端：Python 3.10+, FastAPI, Uvicorn, WebSockets
- 核心引擎：PyTorch (CUDA), manga-image-translator
- 打包与环境：requirements.txt (包含 Windows CUDA 特殊安装指引)

---

### Task 1: Initialize Project Structure & Environment Setup

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/main.py`
- Create: `frontend/package.json`
- Create: `README.md`

- [ ] **Step 1: 创建后端基础依赖文件 (requirements.txt)**

编写后端所需的依赖，特别注明 Windows 环境下安装 PyTorch CUDA 版本的独立命令。

```text
fastapi>=0.100.0
uvicorn>=0.23.0
websockets>=11.0.3
python-multipart>=0.0.6
aiofiles>=23.2.1
pydantic>=2.1.1
# 注意：PyTorch 和 manga-image-translator 需要额外单独安装
```

- [ ] **Step 2: 编写 README 说明文档**

在 `README.md` 中写明 Windows + CUDA 的安装前置步骤：

```markdown
# Manga Translator WebUI

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
```

- [ ] **Step 3: 初始化后端 FastAPI 应用框架**

创建 `backend/main.py`，配置跨域资源共享 (CORS)。

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI(title="Manga Translator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 确保输出目录存在并挂载静态文件
os.makedirs("output_images", exist_ok=True)
app.mount("/output", StaticFiles(directory="output_images"), name="output")

@app.get("/api/status")
async def get_status():
    return {"status": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

- [ ] **Step 4: 测试基础运行状态**

Run: `cd backend && uvicorn main:app --reload`
Expected: 终端显示 Application startup complete. 访问 `http://localhost:8000/api/status` 返回 `{"status": "running"}`。

- [ ] **Step 5: Commit 初始化配置**

```bash
git add backend/requirements.txt backend/main.py README.md
git commit -m "chore: init project structure and fastapi setup"
```

---

### Task 2: Implement File Upload & Extraction Logic

**Files:**
- Modify: `backend/main.py`
- Create: `backend/utils/file_handler.py`

- [ ] **Step 1: 创建文件处理工具类**

编写解压 cbz/zip 文件的逻辑，提取出内部的图片列表。

```python
# backend/utils/file_handler.py
import zipfile
import os
import shutil
import uuid

def extract_archive(file_path: str, extract_dir: str) -> list[str]:
    images = []
    if zipfile.is_zipfile(file_path):
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    images.append(os.path.join(root, file))
    return sorted(images)
```

- [ ] **Step 2: 编写文件上传 API**

在 `main.py` 中添加上传路由，将上传的压缩包存入临时文件夹并解压。

```python
# append to backend/main.py
from fastapi import UploadFile, File, HTTPException
from utils.file_handler import extract_archive
import shutil
import uuid

os.makedirs("temp_uploads", exist_ok=True)
os.makedirs("temp_extracted", exist_ok=True)

@app.post("/api/upload")
async def upload_comic(file: UploadFile = File(...)):
    if not file.filename.endswith(('.zip', '.cbz', '.jpg', '.png')):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    session_id = str(uuid.uuid4())
    file_path = f"temp_uploads/{session_id}_{file.filename}"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extract_dir = f"temp_extracted/{session_id}"
    os.makedirs(extract_dir, exist_ok=True)

    # 如果是压缩包则解压，是单图则直接返回单图路径
    images = []
    if file.filename.endswith(('.zip', '.cbz')):
        images = extract_archive(file_path, extract_dir)
    else:
        images = [file_path]

    return {
        "session_id": session_id,
        "total_images": len(images),
        "source_images": images
    }
```

- [ ] **Step 3: 测试上传 API**

Run: 使用 curl 或 postman 上传一个测试的 zip 压缩包。
Expected: 返回包含 `session_id` 和内部图片列表的 JSON。

- [ ] **Step 4: Commit 上传与解压逻辑**

```bash
git add backend/main.py backend/utils/file_handler.py
git commit -m "feat: implement file upload and cbz/zip extraction"
```

---

### Task 3: Integrate Manga-Image-Translator Engine

**Files:**
- Create: `backend/engine/translator.py`

- [ ] **Step 1: 封装引擎调用接口**

这里通过子进程或者直接 Import `manga_translator` 的核心代码来进行单张图片的翻译（注：此文件假设用户已经安装了 manga-image-translator 库）。

```python
# backend/engine/translator.py
import asyncio
import os
# 导入 manga-image-translator 的核心调度器 (伪代码结构，根据实际 API 调整)
# from manga_translator import MangaTranslator

class TranslatorEngine:
    def __init__(self):
        # 实际使用中需要在此处初始化模型，将其挂载到 CUDA
        pass

    async def translate_image(self, image_path: str, output_path: str, config: dict):
        """
        调用核心引擎翻译单图
        此操作为 GPU 密集型，使用 asyncio.to_thread 避免阻塞主事件循环
        """
        def _run_sync():
            # 伪代码：实际调用 manga_translator 的命令行入口或 Python API
            cmd = f'manga_translator -i "{image_path}" -o "{output_path}" --target-lang {config.get("target_lang", "CHS")} --translator {config.get("translator", "google")} --use-cuda'
            os.system(cmd)

        await asyncio.to_thread(_run_sync)
        return output_path
```

- [ ] **Step 2: Commit 引擎封装**

```bash
git add backend/engine/translator.py
git commit -m "feat: encapsulate manga-image-translator engine wrapper"
```

---

### Task 4: Implement WebSocket Streaming Workflow

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/engine/translator.py`

- [ ] **Step 1: 创建 WebSocket 路由与处理任务**

在 `main.py` 中建立长连接，前端连接后发送 `session_id` 和翻译配置，后端启动后台任务，逐张处理图片并推送进度。

```python
# append to backend/main.py
from fastapi import WebSocket, WebSocketDisconnect
from engine.translator import TranslatorEngine
import json

translator_engine = TranslatorEngine()

@app.websocket("/ws/translate/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    try:
        # 等待前端发送配置与需要处理的图片列表
        data = await websocket.receive_text()
        task_info = json.loads(data)
        images = task_info.get('images', [])
        config = task_info.get('config', {})

        total = len(images)
        await websocket.send_json({"event": "start", "total_pages": total})

        session_out_dir = f"output_images/{session_id}"
        os.makedirs(session_out_dir, exist_ok=True)

        for idx, img_path in enumerate(images):
            # 拼装输出路径
            filename = os.path.basename(img_path)
            out_img_path = os.path.join(session_out_dir, f"trans_{filename}")

            # 核心翻译调用 (GPU 密集型任务)
            try:
                await translator_engine.translate_image(img_path, out_img_path, config)

                # 构建前端可访问的相对 URL
                web_url = f"/output/{session_id}/trans_{filename}"

                # 推送进度和当前图片
                await websocket.send_json({
                    "event": "progress",
                    "current": idx + 1,
                    "total": total,
                    "image_url": web_url
                })
            except Exception as e:
                await websocket.send_json({
                    "event": "error",
                    "current": idx + 1,
                    "message": str(e)
                })

        # 组装完整的 zip 返回下载链接 (可选任务)
        # archive_path = make_archive(...)

        await websocket.send_json({"event": "completed", "download_url": "todo"})

    except WebSocketDisconnect:
        print(f"Client {session_id} disconnected")
```

- [ ] **Step 2: 测试 WebSocket 工作流**

Run: 写一个简单的 Python 脚本或用 wscat 连接测试 WebSocket 逻辑。
Expected: 能够收到 `{"event": "start"}` 和后续的 `progress` 消息。

- [ ] **Step 3: Commit WebSocket 逻辑**

```bash
git add backend/main.py
git commit -m "feat: implement real-time websocket translation streaming"
```

---

### Task 5: Scaffold Frontend Vue3 Application

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/src/main.js`
- Create: `frontend/src/App.vue`
- Create: `frontend/vite.config.js`

- [ ] **Step 1: 初始化 Vite + Vue3 项目**

手动创建或者通过命令行生成一个标准的 Vite+Vue3 骨架，并安装 Tailwind CSS。

```bash
cd frontend
npm init -y
npm install vue
npm install -D vite @vitejs/plugin-vue tailwindcss postcss autoprefixer
npx tailwindcss init -p
```

- [ ] **Step 2: 配置 Tailwind CSS**

修改 `tailwind.config.js` 扫描所有 Vue 文件。

```javascript
// frontend/tailwind.config.js
module.exports = {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: { extend: {} },
  plugins: [],
}
```

- [ ] **Step 3: 构建基础上传与画廊组件**

编写 `src/App.vue`。包含：上传文件的 Input，高级参数的表单（目标语言、翻译器选择），以及通过原生 WebSocket 连接后端，实时将收到的 `image_url` 放入数组渲染在页面上。

```vue
<!-- frontend/src/App.vue 核心骨架 -->
<template>
  <div class="container mx-auto p-4">
    <h1 class="text-2xl font-bold mb-4">自动漫画汉化工具 (GPU 加速版)</h1>

    <div v-if="!isProcessing" class="mb-6 p-4 border rounded">
      <input type="file" @change="handleFileUpload" accept=".zip,.cbz,.jpg,.png" class="mb-4 block" />
      <div class="flex gap-4">
        <label>翻译引擎:
          <select v-model="config.translator" class="border p-1">
            <option value="google">Google</option>
            <option value="deepl">DeepL</option>
            <option value="youdao">Youdao</option>
          </select>
        </label>
      </div>
      <button @click="startTranslation" class="mt-4 bg-blue-500 text-white px-4 py-2 rounded">开始翻译</button>
    </div>

    <div v-else class="mb-6">
      <h2 class="text-xl">处理进度: {{ progress.current }} / {{ progress.total }}</h2>
      <div class="w-full bg-gray-200 rounded-full h-2.5 mt-2">
        <div class="bg-blue-600 h-2.5 rounded-full" :style="`width: ${(progress.current / progress.total) * 100}%`"></div>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div v-for="(img, idx) in translatedImages" :key="idx" class="border p-2 rounded shadow">
        <img :src="img" class="w-full h-auto object-contain" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const file = ref(null)
const config = ref({ translator: 'google', target_lang: 'CHS' })
const isProcessing = ref(false)
const progress = ref({ current: 0, total: 100 })
const translatedImages = ref([])

const handleFileUpload = (e) => {
  file.value = e.target.files[0]
}

const startTranslation = async () => {
  if (!file.value) return
  isProcessing.value = true

  // 1. 上传文件获取 session 和 image list
  const formData = new FormData()
  formData.append('file', file.value)
  const res = await fetch('http://localhost:8000/api/upload', { method: 'POST', body: formData })
  const data = await res.json()

  // 2. 建立 WebSocket 连接
  const ws = new WebSocket(`ws://localhost:8000/ws/translate/${data.session_id}`)
  ws.onopen = () => {
    ws.send(JSON.stringify({ images: data.source_images, config: config.value }))
  }

  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.event === 'start') {
      progress.value.total = msg.total_pages
      progress.value.current = 0
    } else if (msg.event === 'progress') {
      progress.value.current = msg.current
      translatedImages.value.push(`http://localhost:8000${msg.image_url}`)
    } else if (msg.event === 'completed') {
      alert('翻译完成！')
    }
  }
}
</script>
```

- [ ] **Step 4: 测试前端**

Run: `cd frontend && npm run dev`
Expected: 页面渲染出上传框和设置面板，点击翻译后能通过 WS 与后端交互。

- [ ] **Step 5: Commit 前端框架**

```bash
git add frontend/
git commit -m "feat: implement vue3 frontend with websocket realtime gallery"
```

---

### Task 6: Final Integration & Polishing

**Files:**
- Modify: `backend/engine/translator.py`
- Modify: `backend/main.py`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: 处理全部完成后的结果打包**

在翻译全部完成后，将翻译好的图片重新打包回 zip 提供下载。

```python
# append to backend/main.py
@app.get("/api/download/{session_id}")
async def download_result(session_id: str):
    import shutil
    out_dir = f"output_images/{session_id}"
    zip_path = f"temp_uploads/{session_id}_translated.zip"
    shutil.make_archive(zip_path.replace('.zip', ''), 'zip', out_dir)
    from fastapi.responses import FileResponse
    return FileResponse(zip_path, filename=f"translated_comic.zip")
```

- [ ] **Step 2: 完善前端容错和加载 UI**

在 `App.vue` 增加下载按钮，并处理 websocket 意外断开和错误提醒。

- [ ] **Step 3: 端到端联调测试**

Run: 开启后端 `uvicorn` 和前端 `vite`，上传真实的包含多张图片的 zip 包，在控制台观察 GPU 显存占用情况，在网页观察图片流式展现。

- [ ] **Step 4: Commit 最终整合**

```bash
git commit -am "feat: add download capability and polish frontend error handling"
```