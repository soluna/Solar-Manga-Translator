from pathlib import Path
import shutil
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from utils.file_handler import extract_archive

app = FastAPI(title="Manga Translator API")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_images"
TEMP_UPLOADS_DIR = BASE_DIR / "temp_uploads"
TEMP_EXTRACTED_DIR = BASE_DIR / "temp_extracted"
ALLOWED_EXTENSIONS = (".zip", ".cbz", ".jpg", ".jpeg", ".png", ".webp")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 确保输出和临时目录存在并挂载静态文件
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_UPLOADS_DIR.mkdir(exist_ok=True)
TEMP_EXTRACTED_DIR.mkdir(exist_ok=True)
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


def stage_images(session_id: str, image_paths: list[str]) -> list[dict[str, str]]:
    session_output_dir = OUTPUT_DIR / session_id
    session_output_dir.mkdir(parents=True, exist_ok=True)

    staged_images: list[dict[str, str]] = []
    for index, image_path in enumerate(image_paths, start=1):
        source_path = Path(image_path)
        suffix = source_path.suffix.lower() or ".png"
        target_path = session_output_dir / f"{index:04d}{suffix}"
        shutil.copy2(source_path, target_path)

        staged_images.append(
            {
                "name": source_path.name,
                "url": f"/output/{session_id}/{target_path.name}",
            }
        )

    return staged_images


@app.get("/api/status")
async def get_status():
    return {"status": "running"}


@app.post("/api/upload")
async def upload_comic(file: UploadFile = File(...)):
    filename = Path(file.filename or "upload").name
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    session_id = str(uuid.uuid4())
    file_path = TEMP_UPLOADS_DIR / f"{session_id}_{filename}"

    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extract_dir = TEMP_EXTRACTED_DIR / session_id
    extract_dir.mkdir(exist_ok=True)

    # 如果是压缩包则解压，是单图则直接返回单图路径
    images = []
    if filename.lower().endswith((".zip", ".cbz")):
        images = extract_archive(str(file_path), str(extract_dir))
    else:
        images = [str(file_path)]

    staged_images = stage_images(session_id, images)

    return {
        "session_id": session_id,
        "total_images": len(staged_images),
        "images": staged_images,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
