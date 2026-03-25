from pathlib import Path
from typing import Any
import shutil
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from engine.translator import TranslatorEngine
from utils.file_handler import extract_archive

app = FastAPI(title="Manga Translator API")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output_images"
TEMP_UPLOADS_DIR = BASE_DIR / "temp_uploads"
TEMP_EXTRACTED_DIR = BASE_DIR / "temp_extracted"
ALLOWED_EXTENSIONS = (".zip", ".cbz", ".jpg", ".jpeg", ".png", ".webp")
FONT_EXTENSIONS = (".ttf", ".ttc", ".otf")
FONT_DIRECTORIES = {
    "project": BASE_DIR.parent / "fonts",
    "builtin": BASE_DIR / "manga-image-translator" / "fonts",
}
SESSIONS: dict[str, dict[str, Any]] = {}
translator_engine = TranslatorEngine(BASE_DIR)

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


def prepare_session_images(session_id: str, image_paths: list[str]) -> tuple[Path, list[dict[str, str]]]:
    source_dir = OUTPUT_DIR / session_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    prepared_images: list[dict[str, str]] = []
    for index, image_path in enumerate(image_paths, start=1):
        source_path = Path(image_path)
        suffix = source_path.suffix.lower() or ".png"
        stored_name = f"{index:04d}{suffix}"
        target_path = source_dir / stored_name
        shutil.copy2(source_path, target_path)

        prepared_images.append(
            {
                "name": source_path.name,
                "stored_name": stored_name,
                "url": f"/output/{session_id}/source/{stored_name}",
            }
        )

    return source_dir, prepared_images


def list_available_fonts() -> list[dict[str, str]]:
    fonts: list[dict[str, str]] = []
    preferred_order = {
        "msyh.ttc": 0,
        "msgothic.ttc": 1,
        "Arial-Unicode-Regular.ttf": 2,
        "NotoSansMonoCJK-VF.ttf.ttc": 3,
    }

    for source, font_dir in FONT_DIRECTORIES.items():
        if not font_dir.exists():
            continue

        font_paths = sorted(
            (
                path for path in font_dir.iterdir()
                if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
            ),
            key=lambda path: (preferred_order.get(path.name, 99), path.name.lower()),
        )

        for path in font_paths:
            source_label = "自定义" if source == "project" else "内置"
            fonts.append(
                {
                    "id": f"{source}:{path.name}",
                    "name": path.name,
                    "label": f"{path.stem} ({source_label})",
                    "source": source,
                }
            )

    return fonts


@app.get("/api/status")
async def get_status():
    return {"status": "running"}


@app.get("/api/fonts")
async def get_fonts():
    return {"fonts": list_available_fonts()}


@app.post("/api/style-regions/{session_id}")
async def inspect_style_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在，请重新上传文件。")

    return await translator_engine.inspect_style_regions(
        session_id=session_id,
        session=session,
        raw_config=(payload or {}).get("config", {}),
    )


@app.post("/api/review-regions/{session_id}")
async def inspect_review_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在，请重新上传文件。")

    return await translator_engine.inspect_translation_regions(
        session_id=session_id,
        session=session,
        raw_config=(payload or {}).get("config", {}),
    )


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

    source_dir, staged_images = prepare_session_images(session_id, images)
    translated_dir = OUTPUT_DIR / session_id / "translated"
    translated_dir.mkdir(parents=True, exist_ok=True)

    SESSIONS[session_id] = {
        "source_dir": str(source_dir),
        "translated_dir": str(translated_dir),
        "source_images": staged_images,
        "download_path": None,
        "translated_output_map": {},
        "rerender_generation": 0,
    }

    return {
        "session_id": session_id,
        "total_images": len(staged_images),
        "images": staged_images,
    }


@app.get("/api/download/{session_id}")
async def download_translated_archive(session_id: str):
    session = SESSIONS.get(session_id)
    if not session or not session.get("download_path"):
        raise HTTPException(status_code=404, detail="Translated archive not found")

    download_path = Path(session["download_path"])
    if not download_path.exists():
        raise HTTPException(status_code=404, detail="Translated archive not found")

    return FileResponse(
        path=download_path,
        media_type="application/zip",
        filename=f"{session_id}_translated.zip",
    )


@app.websocket("/ws/translate/{session_id}")
async def translate_session(websocket: WebSocket, session_id: str):
    await websocket.accept()

    session = SESSIONS.get(session_id)
    if not session:
        await websocket.send_json({"event": "error", "message": "会话不存在，请重新上传文件。"})
        await websocket.close()
        return

    try:
        payload = await websocket.receive_json()
        action = str(payload.get("action") or "translate").strip().lower()
        config = payload.get("config", {})

        async def send_event(event: dict[str, Any]) -> None:
            await websocket.send_json(event)

        if action == "rerender":
            result = await translator_engine.rerender_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
            )
        else:
            result = await translator_engine.translate_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
            )
        await websocket.send_json({"event": "completed", **result})
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": str(exc)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
