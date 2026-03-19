from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from utils.file_handler import extract_archive
from engine.translator import TranslatorEngine
import shutil
import uuid
import os
import json

app = FastAPI(title="Manga Translator API")
translator_engine = TranslatorEngine()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 确保输出和临时目录存在并挂载静态文件
os.makedirs("output_images", exist_ok=True)
os.makedirs("temp_uploads", exist_ok=True)
os.makedirs("temp_extracted", exist_ok=True)
app.mount("/output", StaticFiles(directory="output_images"), name="output")

@app.get("/api/status")
async def get_status():
    return {"status": "running"}

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
            filename = os.path.basename(img_path)
            # manga-translator 会自动在输出目录生成以原文件名为名称的文件
            # 我们直接指定输出目录
            out_img_path = session_out_dir

            try:
                # 核心翻译调用 (GPU 密集型任务)
                await translator_engine.translate_image(img_path, out_img_path, config)

                # 构建前端可访问的相对 URL，这里假设输出文件名与原名一致
                web_url = f"/output/{session_id}/{filename}"

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

        await websocket.send_json({"event": "completed"})

    except WebSocketDisconnect:
        print(f"Client {session_id} disconnected")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
