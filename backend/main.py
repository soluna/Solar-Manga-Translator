from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from utils.file_handler import extract_archive
import shutil
import uuid
import os

app = FastAPI(title="Manga Translator API")

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)