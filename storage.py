import json
import shutil
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from app.config import STORAGE_DIR

METADATA_FILE = STORAGE_DIR / "metadata.json"
NOTEBOOKS_FILE = STORAGE_DIR / "notebooks.json"
INBOX_DIR = STORAGE_DIR / "inbox"
NOTEBOOKS_DIR = STORAGE_DIR / "notebooks"

INBOX_DIR.mkdir(parents=True, exist_ok=True)
NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_NOTEBOOKS = ["General", "Research", "Project Notes", "References"]

def _sanitize_name(name: str) -> str:
    """Sanitize notebook name for safe folder path creation."""
    clean = re.sub(r'[<>:"/\\|?*]', '_', name.strip())
    return clean or "Untitled_Notebook"

def _load_notebooks() -> List[str]:
    if not NOTEBOOKS_FILE.exists():
        _save_notebooks(DEFAULT_NOTEBOOKS)
        return DEFAULT_NOTEBOOKS.copy()
    try:
        with open(NOTEBOOKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else DEFAULT_NOTEBOOKS.copy()
    except Exception:
        return DEFAULT_NOTEBOOKS.copy()

def _save_notebooks(notebooks: List[str]) -> None:
    with open(NOTEBOOKS_FILE, "w", encoding="utf-8") as f:
        json.dump(notebooks, f, indent=2, ensure_ascii=False)

def get_notebooks() -> List[str]:
    """Returns list of active custom topics/notebooks."""
    notebooks = _load_notebooks()
    for nb in notebooks:
        (NOTEBOOKS_DIR / _sanitize_name(nb)).mkdir(parents=True, exist_ok=True)
    return notebooks

def create_notebook(name: str) -> List[str]:
    """Create a new topic section/notebook."""
    clean = name.strip()
    if not clean:
        return get_notebooks()
    notebooks = _load_notebooks()
    if clean not in notebooks and clean.lower() != "inbox":
        notebooks.append(clean)
        _save_notebooks(notebooks)
        (NOTEBOOKS_DIR / _sanitize_name(clean)).mkdir(parents=True, exist_ok=True)
    return notebooks

def delete_notebook(name: str) -> List[str]:
    """Delete a notebook and move its items back to Inbox."""
    notebooks = _load_notebooks()
    clean = name.strip()
    if clean in notebooks:
        notebooks.remove(clean)
        _save_notebooks(notebooks)

        # Move screenshots from this notebook to Inbox
        metadata = _load_metadata()
        for item in metadata.values():
            if item.get("notebook") == clean:
                move_screenshot(item["id"], "Inbox")

        # Cleanup folder
        folder = NOTEBOOKS_DIR / _sanitize_name(clean)
        if folder.exists():
            try:
                shutil.rmtree(folder)
            except Exception:
                pass
    return notebooks

def _load_metadata() -> Dict[str, Any]:
    if not METADATA_FILE.exists():
        return {}
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_metadata(data: Dict[str, Any]) -> None:
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def store_screenshot(
    file_path: Path,
    notebook: str = "Inbox",
    ocr_text: str = "",
    notes: str = ""
) -> Dict[str, Any]:
    """
    Store an uncropped raw screenshot in the designated notebook or Inbox.
    Zero image manipulation or AI cropping is applied.
    """
    clean_nb = _sanitize_name(notebook)
    if clean_nb.lower() == "inbox":
        target_dir = INBOX_DIR
        actual_notebook = "Inbox"
        is_triaged = False
    else:
        target_dir = NOTEBOOKS_DIR / clean_nb
        target_dir.mkdir(parents=True, exist_ok=True)
        actual_notebook = notebook
        is_triaged = True

    item_id = str(uuid.uuid4())[:8]
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_filename = f"{timestamp_str}_{item_id}_{file_path.name}"
    dest_path = target_dir / dest_filename

    shutil.copy2(file_path, dest_path)

    metadata = _load_metadata()
    record = {
        "id": item_id,
        "filename": dest_filename,
        "notebook": actual_notebook,
        "triaged": is_triaged,
        "original_name": file_path.name,
        "stored_path": str(dest_path),
        "image_url": f"/api/files/download/{item_id}",
        "ocr_preview": ocr_text[:300].strip() if ocr_text else "",
        "ocr_full": ocr_text or "",
        "notes": notes or "",
        "created_at": datetime.now().isoformat(),
        "voice_note": None
    }

    metadata[item_id] = record
    _save_metadata(metadata)
    return record

def move_screenshot(item_id: str, target_notebook: str) -> Optional[Dict[str, Any]]:
    """
    Move a screenshot (and its linked audio) between notebooks or from Inbox to a topic.
    Supports both drag-and-drop and one-click filing.
    """
    metadata = _load_metadata()
    record = metadata.get(item_id)
    if not record:
        return None

    clean_target = _sanitize_name(target_notebook)
    if clean_target.lower() == "inbox":
        target_dir = INBOX_DIR
        actual_target = "Inbox"
        is_triaged = False
    else:
        target_dir = NOTEBOOKS_DIR / clean_target
        target_dir.mkdir(parents=True, exist_ok=True)
        actual_target = target_notebook
        is_triaged = True

    current_path = Path(record["stored_path"])
    new_path = target_dir / current_path.name

    if current_path.exists() and current_path != new_path:
        shutil.move(str(current_path), str(new_path))

    record["stored_path"] = str(new_path)
    record["notebook"] = actual_target
    record["triaged"] = is_triaged

    # Move voice note file if attached
    if record.get("voice_note") and record["voice_note"].get("stored_path"):
        vn_path = Path(record["voice_note"]["stored_path"])
        if vn_path.exists():
            new_vn_path = target_dir / vn_path.name
            shutil.move(str(vn_path), str(new_vn_path))
            record["voice_note"]["stored_path"] = str(new_vn_path)

    metadata[item_id] = record
    _save_metadata(metadata)
    return record

def update_notes(item_id: str, notes: str) -> Optional[Dict[str, Any]]:
    """Update textual context notes attached to a screenshot."""
    metadata = _load_metadata()
    record = metadata.get(item_id)
    if not record:
        return None
    record["notes"] = notes
    metadata[item_id] = record
    _save_metadata(metadata)
    return record

def attach_voice_note(item_id: str, audio_bytes: bytes, extension: str = "webm") -> Optional[Dict[str, Any]]:
    """Save an audio memo linked permanently to the screenshot."""
    metadata = _load_metadata()
    record = metadata.get(item_id)
    if not record:
        return None

    stored_file_path = Path(record["stored_path"])
    target_dir = stored_file_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    image_stem = stored_file_path.stem
    voice_filename = f"{image_stem}_voice.{extension}"
    voice_path = target_dir / voice_filename

    with open(voice_path, "wb") as f:
        f.write(audio_bytes)

    voice_data = {
        "filename": voice_filename,
        "stored_path": str(voice_path),
        "audio_url": f"/api/files/download-audio/{item_id}",
        "created_at": datetime.now().isoformat()
    }

    record["voice_note"] = voice_data
    metadata[item_id] = record
    _save_metadata(metadata)
    return record

def delete_screenshot(item_id: str) -> bool:
    """Permanently delete a screenshot and its audio memo."""
    metadata = _load_metadata()
    record = metadata.get(item_id)
    if not record:
        return False

    try:
        img_p = Path(record["stored_path"])
        if img_p.exists():
            img_p.unlink()
    except Exception:
        pass

    if record.get("voice_note") and record["voice_note"].get("stored_path"):
        try:
            vn_p = Path(record["voice_note"]["stored_path"])
            if vn_p.exists():
                vn_p.unlink()
        except Exception:
            pass

    del metadata[item_id]
    _save_metadata(metadata)
    return True

def get_screenshot(item_id: str) -> Optional[Dict[str, Any]]:
    metadata = _load_metadata()
    return metadata.get(item_id)

def get_latest_triage_screenshot() -> Optional[Dict[str, Any]]:
    """Returns the most recent unfiled screenshot in Inbox, or the latest screenshot."""
    metadata = _load_metadata()
    if not metadata:
        return None
    # Prioritize untriaged inbox captures
    inbox_items = [v for v in metadata.values() if v.get("notebook") == "Inbox" or not v.get("triaged")]
    if inbox_items:
        return sorted(inbox_items, key=lambda x: x["created_at"], reverse=True)[0]
    return sorted(metadata.values(), key=lambda x: x["created_at"], reverse=True)[0]

def list_screenshots(notebook: Optional[str] = None) -> List[Dict[str, Any]]:
    """List screenshots optionally filtered by notebook/topic."""
    metadata = _load_metadata()
    items = sorted(metadata.values(), key=lambda x: x["created_at"], reverse=True)
    if notebook:
        if notebook.lower() == "all":
            return items
        return [i for i in items if i.get("notebook") == notebook]
    return items

def get_notebook_counts() -> Dict[str, int]:
    """Returns count of screenshots in Inbox and each notebook."""
    metadata = _load_metadata()
    counts = {"Inbox": 0, "All": len(metadata)}
    for nb in get_notebooks():
        counts[nb] = 0
    for item in metadata.values():
        nb = item.get("notebook", "Inbox")
        counts[nb] = counts.get(nb, 0) + 1
    return counts

def search_screenshots(query: str) -> List[Dict[str, Any]]:
    """Search screenshots across OCR text, notes, filename, or topic."""
    metadata = _load_metadata()
    q = query.lower().strip()
    if not q:
        return sorted(metadata.values(), key=lambda x: x["created_at"], reverse=True)[:15]
    results = []
    for item in sorted(metadata.values(), key=lambda x: x["created_at"], reverse=True):
        ocr = (item.get("ocr_full") or "").lower()
        fn = (item.get("original_name") or "").lower()
        nb = (item.get("notebook") or "").lower()
        notes = (item.get("notes") or "").lower()
        if q in ocr or q in fn or q in nb or q in notes:
            results.append(item)
    return results
