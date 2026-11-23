import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import PROJECT_ROOT, STORAGE_DIR, WATCH_DIRS
from app.storage import (
    get_notebooks,
    create_notebook,
    delete_notebook,
    store_screenshot,
    move_screenshot,
    update_notes,
    attach_voice_note,
    delete_screenshot,
    get_screenshot,
    list_screenshots,
    get_latest_triage_screenshot,
    get_notebook_counts,
    _load_metadata,
    _save_metadata
)
from app.ocr_engine import extract_text

app = FastAPI(title="ScreenVault — NotebookLM-Style Screenshot Manager")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

active_connections: List[WebSocket] = []
loop: asyncio.AbstractEventLoop = None

@app.on_event("startup")
async def startup_event():
    global loop
    loop = asyncio.get_running_loop()

STATIC_DIR = PROJECT_ROOT / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

# 1. File Serving
@app.get("/api/files/download/{item_id}")
async def serve_screenshot_file(item_id: str):
    record = get_screenshot(item_id)
    if not record or not Path(record["stored_path"]).exists():
        raise HTTPException(status_code=404, detail="Screenshot file not found")
    return FileResponse(record["stored_path"])

@app.get("/api/files/download-audio/{item_id}")
async def serve_audio_file(item_id: str):
    record = get_screenshot(item_id)
    if not record or not record.get("voice_note") or not Path(record["voice_note"]["stored_path"]).exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(record["voice_note"]["stored_path"])

# 2. Notebooks Management
@app.get("/api/notebooks")
async def get_all_notebooks():
    notebooks = get_notebooks()
    counts = get_notebook_counts()
    return {"status": "success", "notebooks": notebooks, "counts": counts}

@app.post("/api/notebooks")
async def create_new_notebook(payload: dict):
    name = payload.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Notebook name cannot be empty")
    notebooks = create_notebook(name)
    counts = get_notebook_counts()
    broadcast_sync({"type": "NOTEBOOKS_UPDATED", "notebooks": notebooks, "counts": counts})
    return {"status": "success", "notebooks": notebooks, "counts": counts}

@app.delete("/api/notebooks/{name}")
async def delete_existing_notebook(name: str):
    notebooks = delete_notebook(name)
    counts = get_notebook_counts()
    broadcast_sync({"type": "NOTEBOOKS_UPDATED", "notebooks": notebooks, "counts": counts})
    return {"status": "success", "notebooks": notebooks, "counts": counts}

# 3. Screenshot Listing & Filtering
@app.get("/api/screenshots")
async def list_items(notebook: Optional[str] = None):
    items = list_screenshots(notebook=notebook)
    return {"status": "success", "data": items, "counts": get_notebook_counts()}

@app.get("/api/screenshots/{item_id}")
async def get_single_screenshot(item_id: str):
    record = get_screenshot(item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return {"status": "success", "data": record}

@app.get("/api/latest-triage")
async def get_triage_item():
    latest = get_latest_triage_screenshot()
    return {"status": "success", "data": latest, "notebooks": get_notebooks(), "counts": get_notebook_counts()}

# 4. Drag-and-Drop & One-Click Topic Routing
@app.post("/api/move-screenshot")
async def move_item(payload: dict):
    item_id = payload.get("id")
    target_notebook = payload.get("notebook")
    if not item_id or not target_notebook:
        raise HTTPException(status_code=400, detail="Missing screenshot ID or target notebook")

    updated = move_screenshot(item_id, target_notebook)
    if not updated:
        raise HTTPException(status_code=404, detail="Screenshot not found")

    counts = get_notebook_counts()
    broadcast_sync({"type": "SCREENSHOT_MOVED", "data": updated, "counts": counts})
    return {"status": "success", "data": updated, "counts": counts}

# 5. Context Notes & Voice Memos
@app.post("/api/screenshots/{item_id}/notes")
async def update_screenshot_notes(item_id: str, payload: dict):
    notes = payload.get("notes", "")
    updated = update_notes(item_id, notes)
    if not updated:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return {"status": "success", "data": updated}

@app.post("/api/voice-note")
async def upload_voice_note(
    screenshot_id: str = Form(...),
    audio: UploadFile = File(...)
):
    try:
        content = await audio.read()
        ext = "webm"
        if audio.filename and "." in audio.filename:
            ext = audio.filename.rsplit(".", 1)[-1]
        
        updated = attach_voice_note(screenshot_id, content, extension=ext)
        if not updated:
            raise HTTPException(status_code=404, detail="Screenshot record not found")
        
        return {"status": "success", "data": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Voice upload failed: {str(e)}")

# 6. Direct Paste Support (paste into active notebook)
@app.post("/api/paste")
async def paste_screenshot(
    file: UploadFile = File(...),
    notebook: str = Query("Inbox")
):
    temp_dir = STORAGE_DIR / "inbox"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / file.filename
    content = await file.read()
    with open(temp_path, "wb") as f:
        f.write(content)

    ocr_text = extract_text(temp_path)
    record = store_screenshot(temp_path, notebook=notebook, ocr_text=ocr_text)

    # Clean temp file after storing
    if temp_path.exists():
        temp_path.unlink()

    counts = get_notebook_counts()
    broadcast_sync({"type": "NEW_CAPTURE", "data": record, "counts": counts})
    return {"status": "success", "data": record, "counts": counts}

# 7. Manual Image Editing Save
@app.post("/api/screenshots/{item_id}/image")
async def update_screenshot_image(item_id: str, image: UploadFile = File(...)):
    """Receives user-edited image (cropped, rotated, annotated) and updates storage."""
    record = get_screenshot(item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    content = await image.read()
    target_path = Path(record["stored_path"])
    with open(target_path, "wb") as f:
        f.write(content)

    # Re-run OCR cleanly on the manually modified image
    new_ocr = extract_text(target_path)
    record["ocr_full"] = new_ocr
    record["ocr_preview"] = new_ocr[:300].strip() if new_ocr else ""

    metadata = _load_metadata()
    metadata[item_id] = record
    _save_metadata(metadata)

    broadcast_sync({"type": "IMAGE_MODIFIED", "data": record})
    return {"status": "success", "data": record}

# 8. Spotlight Quick Search
@app.get("/api/search")
async def spotlight_search(q: str = Query("")):
    """Instant search across all notebooks, notes, OCR text, and filenames."""
    query = q.lower().strip()
    metadata = _load_metadata()
    if not query:
        return {"status": "success", "data": []}

    matches = []
    for item in sorted(metadata.values(), key=lambda x: x["created_at"], reverse=True):
        ocr = (item.get("ocr_full") or "").lower()
        notes = (item.get("notes") or "").lower()
        notebook = (item.get("notebook") or "").lower()
        fn = (item.get("original_name") or "").lower()

        if query in notes or query in ocr or query in notebook or query in fn:
            snippet = ""
            if query in notes:
                snippet = f"Note: {item.get('notes')[:120]}"
            elif query in ocr:
                snippet = f"Text: {item.get('ocr_preview')[:120]}"
            else:
                snippet = f"In {item.get('notebook')} • {item.get('original_name')}"

            matches.append({
                "id": item["id"],
                "filename": item["filename"],
                "notebook": item.get("notebook", "General"),
                "image_url": item["image_url"],
                "snippet": snippet,
                "created_at": item["created_at"]
            })

    return {"status": "success", "data": matches[:20]}

# 7. Utilities (Open in Explorer & Delete)
@app.post("/api/open-folder")
async def open_file_folder(payload: dict):
    item_id = payload.get("id")
    if not item_id:
        raise HTTPException(status_code=400, detail="Missing screenshot ID")
    record = get_screenshot(item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    
    file_path = Path(record["stored_path"]).resolve()
    try:
        if os.name == 'nt':
            subprocess.Popen(f'explorer /select,"{file_path}"')
        else:
            subprocess.Popen(['open', str(file_path.parent)])
        return {"status": "success", "message": "Opened in File Explorer"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {str(e)}")

@app.delete("/api/screenshots/{item_id}")
async def delete_single_screenshot(item_id: str):
    success = delete_screenshot(item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    counts = get_notebook_counts()
    next_item = get_latest_triage_screenshot()
    broadcast_sync({"type": "SCREENSHOT_DELETED", "id": item_id, "counts": counts})
    return {"status": "success", "message": "Deleted", "next": next_item, "counts": counts}

# 8. WebSockets
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)

def broadcast_sync(payload: dict):
    global loop
    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(_broadcast_to_clients(payload), loop)

async def _broadcast_to_clients(payload: dict):
    message = json.dumps(payload)
    for conn in list(active_connections):
        try:
            await conn.send_text(message)
        except Exception:
            if conn in active_connections:
                active_connections.remove(conn)
