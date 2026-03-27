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


@app.get("/api/projects")
async def list_projects():
    return {"projects": translator_engine.list_projects()}


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: dict[str, Any] | None = None):
    session = SESSIONS.get(project_id)
    if not session:
        try:
            session = translator_engine.restore_project_session(project_id)
            SESSIONS[project_id] = session
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    payload = payload or {}
    summary = translator_engine.update_project_metadata(
        project_id=project_id,
        session=session,
        title=payload.get("title"),
        note=payload.get("note"),
    )
    return {"project": summary}


@app.post("/api/projects/{project_id}/restore")
async def restore_project(project_id: str):
    try:
        session = translator_engine.restore_project_session(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    SESSIONS[project_id] = session
    return translator_engine.build_client_session_payload(project_id, session)


@app.get("/api/projects/{project_id}/snapshots")
async def list_project_snapshots(project_id: str):
    return {"snapshots": translator_engine.list_project_snapshots(project_id)}


@app.post("/api/projects/{project_id}/snapshots/{snapshot_id}/restore")
async def restore_project_snapshot(project_id: str, snapshot_id: str):
    try:
        restored_project_id, session = translator_engine.restore_snapshot_as_project(project_id, snapshot_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    SESSIONS[restored_project_id] = session
    return translator_engine.build_client_session_payload(restored_project_id, session)


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


@app.post("/api/manual-regions/{session_id}")
async def update_manual_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在，请重新上传文件。")

    payload = payload or {}
    action = str(payload.get("action") or "create").strip().lower()

    try:
        if action == "delete":
            region_id = str(payload.get("region_id") or "").strip()
            if not region_id:
                raise HTTPException(status_code=400, detail="缺少需要删除的补漏框 ID。")
            removed = translator_engine.delete_manual_region(session, region_id)
            if not removed:
                raise HTTPException(status_code=404, detail="没有找到对应的补漏框。")
            translator_engine.persist_project_state(
                session_id,
                session,
                snapshot_kind="manual_region_deleted",
                snapshot_summary="删除手动补漏框",
            )
            return {"ok": True, "action": "delete", "region_id": region_id}

        if action == "merge":
            translator_engine.capture_session_config(session, payload.get("config", {}))
            stored_name = str(payload.get("stored_name") or "").strip()
            region_ids = payload.get("region_ids") or []
            if not stored_name:
                raise HTTPException(status_code=400, detail="缺少目标页面信息。")
            if not isinstance(region_ids, list) or len(region_ids) < 2:
                raise HTTPException(status_code=400, detail="至少需要选择两个文本框才能合并。")
            region = await translator_engine.merge_regions(
                session_id=session_id,
                session=session,
                raw_config=payload.get("config", {}),
                stored_name=stored_name,
                region_ids=region_ids,
            )
            translator_engine.persist_project_state(
                session_id,
                session,
                snapshot_kind="regions_merged",
                snapshot_summary=f"{stored_name} 合并文本框",
            )
            return {"ok": True, "action": "merge", "region": region}

        translator_engine.capture_session_config(session, payload.get("config", {}))
        stored_name = str(payload.get("stored_name") or "").strip()
        if not stored_name:
            raise HTTPException(status_code=400, detail="缺少目标页面信息。")

        region = await translator_engine.create_manual_region(
            session_id=session_id,
            session=session,
            raw_config=payload.get("config", {}),
            stored_name=stored_name,
            bbox=payload.get("bbox"),
        )
        translator_engine.persist_project_state(
            session_id,
            session,
            snapshot_kind="manual_region_added",
            snapshot_summary=f"{stored_name} 新增手动补漏框",
        )
        return {"ok": True, "action": "create", "region": region}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
        "manual_regions": {},
        "workflow_stage": "idle",
    }
    translator_engine.initialize_project(
        session_id,
        SESSIONS[session_id],
        title=Path(filename).stem,
    )

    return translator_engine.build_client_session_payload(session_id, SESSIONS[session_id])


@app.get("/api/download/{session_id}")
async def download_translated_archive(session_id: str):
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Translated archive not found")

    download_path = Path(
        translator_engine.build_session_archive(
            session_id=session_id,
            session=session,
        )
    )
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
        target_stored_name = str(payload.get("target_stored_name") or "").strip()

        async def send_event(event: dict[str, Any]) -> None:
            await websocket.send_json(event)

        if action == "rerender":
            result = await translator_engine.rerender_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
                target_stored_name=target_stored_name or None,
            )
        elif action == "detect":
            result = await translator_engine.detect_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
            )
        elif action == "resume-translate":
            result = await translator_engine.resume_translation_session(
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
        await websocket.send_json(
            {
                "event": "completed",
                **translator_engine.build_client_session_payload(session_id, session),
                **result,
            }
        )
    except WebSocketDisconnect:
        return
    except Exception as exc:
        await websocket.send_json({"event": "error", "message": str(exc)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
