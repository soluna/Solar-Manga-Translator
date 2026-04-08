from pathlib import Path
from typing import Any
import shutil
import uuid
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
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
    "project": (
        BASE_DIR.parent / "fonts",
        BASE_DIR.parent / "font",
    ),
    "builtin": (
        BASE_DIR / "manga-image-translator" / "fonts",
    ),
}


def iter_font_directories(source: str) -> list[Path]:
    candidates = FONT_DIRECTORIES.get(source) or ()
    directories: list[Path] = []
    seen: set[str] = set()
    for directory in candidates:
        normalized = str(directory.resolve()) if directory.exists() else str(directory)
        if normalized in seen:
            continue
        seen.add(normalized)
        directories.append(directory)
    return directories
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

    for source in FONT_DIRECTORIES:
        seen_names: set[str] = set()
        for font_dir in iter_font_directories(source):
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
                if path.name in seen_names:
                    continue
                seen_names.add(path.name)
                source_label = "自定义" if source == "project" else "内置"
                suffix = path.suffix.lower()
                format_hint = {
                    ".ttf": "truetype",
                    ".otf": "opentype",
                }.get(suffix, "")
                fonts.append(
                    {
                        "id": f"{source}:{path.name}",
                        "name": path.name,
                        "label": f"{path.stem} ({source_label})",
                        "source": source,
                        "extension": suffix,
                        "format_hint": format_hint,
                        "url": f"/api/fonts/file/{source}/{quote(path.name)}",
                    }
                )

    return fonts


def resolve_font_file(source: str, font_name: str) -> Path:
    if Path(font_name).name != font_name:
        raise HTTPException(status_code=404, detail="字体文件不存在。")
    for font_dir in iter_font_directories(source):
        if not font_dir.exists():
            continue
        target_path = (font_dir / font_name).resolve()
        if target_path.exists() and target_path.parent == font_dir.resolve():
            return target_path
    raise HTTPException(status_code=404, detail="字体文件不存在。")


def get_or_restore_session(project_id: str) -> dict[str, Any]:
    session = SESSIONS.get(project_id)
    if session:
        return session

    try:
        session = translator_engine.restore_project_session(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    SESSIONS[project_id] = session
    return session


def preserve_single_upload_name(temp_path: Path, target_dir: Path, filename: str) -> str:
    target_path = target_dir / filename
    shutil.copy2(temp_path, target_path)
    return str(target_path)


@app.get("/api/status")
async def get_status():
    return {"status": "running"}


@app.get("/api/fonts")
async def get_fonts():
    return {"fonts": list_available_fonts()}


@app.get("/api/fonts/file/{source}/{font_name:path}")
async def get_font_file(source: str, font_name: str):
    font_path = resolve_font_file(source, font_name)
    media_type = {
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".ttc": "font/collection",
    }.get(font_path.suffix.lower())
    return FileResponse(font_path, media_type=media_type)


@app.get("/api/projects")
async def list_projects():
    return {"projects": translator_engine.list_projects()}


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)

    payload = payload or {}
    summary = translator_engine.update_project_metadata(
        project_id=project_id,
        session=session,
        title=payload.get("title"),
        note=payload.get("note"),
        review_mode=payload.get("review_mode"),
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


@app.post("/api/projects/{project_id}/base-images")
async def upload_project_base_images(project_id: str, file: UploadFile = File(...)):
    session = get_or_restore_session(project_id)
    if translator_engine.is_session_busy(project_id):
        raise HTTPException(status_code=409, detail="该项目当前有任务在运行，请稍后再补充无字图。")

    filename = Path(file.filename or "upload").name
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    upload_token = str(uuid.uuid4())
    file_path = TEMP_UPLOADS_DIR / f"{upload_token}_{filename}"
    with file_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    extract_dir = TEMP_EXTRACTED_DIR / f"{project_id}_base_{upload_token}"
    extract_dir.mkdir(exist_ok=True)

    images = []
    if filename.lower().endswith((".zip", ".cbz")):
        images = extract_archive(str(file_path), str(extract_dir))
    else:
        images = [preserve_single_upload_name(file_path, extract_dir, filename)]

    try:
        result = translator_engine.attach_base_images(project_id, session, images)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        **translator_engine.build_client_session_payload(project_id, session),
        "base_image_upload": result,
    }


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


@app.post("/api/projects/{project_id}/snapshots/{snapshot_id}/pin")
async def pin_project_snapshot(project_id: str, snapshot_id: str, payload: dict[str, Any] | None = None):
    payload = payload or {}
    try:
        snapshots = translator_engine.set_snapshot_pinned(
            project_id=project_id,
            snapshot_id=snapshot_id,
            pinned=bool(payload.get("pinned", True)),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"snapshots": snapshots}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        translator_engine.delete_project(project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    SESSIONS.pop(project_id, None)
    return {"ok": True}


@app.post("/api/style-regions/{session_id}")
async def inspect_style_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)

    return await translator_engine.inspect_style_regions(
        session_id=session_id,
        session=session,
        raw_config=(payload or {}).get("config", {}),
    )


@app.post("/api/review-regions/{session_id}")
async def inspect_review_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)

    return await translator_engine.inspect_translation_regions(
        session_id=session_id,
        session=session,
        raw_config=(payload or {}).get("config", {}),
    )


@app.post("/api/manual-regions/{session_id}")
async def update_manual_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)

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
                persist_page_documents=True,
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
                persist_page_documents=True,
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
            persist_page_documents=True,
        )
        return {"ok": True, "action": "create", "region": region}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/pages/{session_id}/{page_id}/document")
async def get_page_document(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        document = translator_engine.get_page_document(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"document": document}


@app.get("/api/pages/{session_id}/{page_id}/base-image")
async def get_page_base_image(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        base_path = translator_engine.get_page_base_image_path(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=base_path)


@app.get("/api/pages/{session_id}/{page_id}/source-image")
async def get_page_source_image(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        source_path = translator_engine.get_page_source_image_path(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=source_path)


@app.get("/api/pages/{session_id}/{page_id}/preview-image")
async def get_page_preview_image(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        preview_path = translator_engine.get_page_preview_image_path(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=preview_path)


@app.get("/api/pages/{session_id}/{page_id}/translated-image")
async def get_page_translated_image(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        translated_path = translator_engine.get_page_translated_image_path(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=translated_path)


@app.get("/api/pages/{session_id}/{page_id}/ocr-debug")
async def get_page_ocr_debug(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        payload = translator_engine.get_page_ocr_debug(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.get("/api/pages/{session_id}/{page_id}/translation-input-debug")
async def get_page_translation_input_debug(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        payload = translator_engine.get_page_translation_input_debug(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.get("/api/projects/{project_id}/translation-request-debug")
async def get_project_translation_request_debug(project_id: str):
    _ = get_or_restore_session(project_id)
    try:
        payload = translator_engine.get_translation_request_debug(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.post("/api/pages/{session_id}/{page_id}/commands")
async def apply_page_commands(session_id: str, page_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}

    try:
        result = await translator_engine.apply_page_commands(
            project_id=session_id,
            session=session,
            page_id=page_id,
            raw_config=payload.get("config", {}),
            commands=payload.get("commands") or [],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@app.post("/api/upload")
async def upload_comic(
    file: UploadFile = File(...),
    review_mode: str = Form(TranslatorEngine.DEFAULT_REVIEW_MODE),
):
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
        images = [preserve_single_upload_name(file_path, extract_dir, filename)]

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
        "review_mode": review_mode,
    }
    translator_engine.initialize_project(
        session_id,
        SESSIONS[session_id],
        title=Path(filename).stem,
        review_mode=review_mode,
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

    try:
        session = get_or_restore_session(session_id)
    except HTTPException:
        await websocket.send_json({"event": "error", "message": "会话不存在，请重新上传文件。"})
        await websocket.close()
        return

    try:
        payload = await websocket.receive_json()
        action = str(payload.get("action") or "translate").strip().lower()
        config = payload.get("config", {})
        target_stored_name = str(payload.get("target_stored_name") or "").strip()

        if translator_engine.is_session_busy(session_id):
            await websocket.send_json({"event": "error", "message": "该项目已有任务在运行，请等待当前任务完成。"})
            await websocket.close()
            return

        translator_engine.mark_session_busy(session_id, action)

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
        elif action == "translate-page":
            result = await translator_engine.resume_translation_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
                target_stored_name=target_stored_name or None,
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
    finally:
        translator_engine.clear_session_busy(session_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
