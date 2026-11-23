import os
import time
import hashlib
import threading
from pathlib import Path
from typing import Callable, Optional, List
from datetime import datetime

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PIL import Image, ImageGrab

from app.config import WATCH_DIRS, FALLBACK_WATCH_DIR, PROJECT_ROOT
from app.ocr_engine import extract_text
from app.storage import store_screenshot, get_latest_triage_screenshot, get_notebook_counts, get_notebooks

VALID_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

def wait_for_file_ready(file_path: Path, timeout: float = 3.0) -> bool:
    """Ensure the operating system has finished writing the screenshot file."""
    start_time = time.time()
    last_size = -1
    while time.time() - start_time < timeout:
        try:
            if not file_path.exists():
                return False
            current_size = file_path.stat().st_size
            if current_size > 0 and current_size == last_size:
                with open(file_path, "rb") as f:
                    f.read(1024)
                return True
            last_size = current_size
        except (PermissionError, OSError):
            pass
        time.sleep(0.2)
    return file_path.exists() and file_path.stat().st_size > 0

class ScreenshotProcessor:
    """Processes captured screenshots quietly in the background without auto-cropping."""
    def __init__(self, on_new_capture: Optional[Callable] = None):
        self.on_new_capture = on_new_capture
        self._processed_files = set()
        self._lock = threading.Lock()

    def process(self, path: Path, notebook: str = "Inbox") -> Optional[dict]:
        with self._lock:
            resolved_str = str(path.resolve())
            if resolved_str in self._processed_files:
                return None
            self._processed_files.add(resolved_str)

        if not wait_for_file_ready(path):
            return None

        # Extract OCR text for search & quick copying (pure raw extraction, zero image modification)
        ocr_text = extract_text(path)

        # Store in raw, authentic form into Inbox or specified notebook
        record = store_screenshot(path, notebook=notebook, ocr_text=ocr_text)
        print(f"[Quiet Capture] Filed into '{record['notebook']}': {record['filename']}")

        if self.on_new_capture:
            try:
                self.on_new_capture(record)
            except Exception as e:
                print(f"[Capture Notification Error] {e}")

        return record

class ScreenshotEventHandler(FileSystemEventHandler):
    def __init__(self, processor: ScreenshotProcessor):
        super().__init__()
        self.processor = processor

    def on_created(self, event):
        if not event.is_directory:
            p = Path(event.src_path)
            if p.suffix.lower() in VALID_EXTENSIONS:
                threading.Thread(target=self.processor.process, args=(p,), daemon=True).start()

    def on_moved(self, event):
        if not event.is_directory:
            p = Path(event.dest_path)
            if p.suffix.lower() in VALID_EXTENSIONS:
                threading.Thread(target=self.processor.process, args=(p,), daemon=True).start()

class ClipboardWatcher(threading.Thread):
    """
    Quietly monitors Windows Clipboard for new screenshots.
    Saves raw images into the Inbox without disturbing user workflow.
    """
    def __init__(self, processor: ScreenshotProcessor, drop_dir: Path = FALLBACK_WATCH_DIR):
        super().__init__(daemon=True)
        self.processor = processor
        self.drop_dir = drop_dir
        self.last_hash: Optional[str] = None
        self.running = True

        try:
            initial = ImageGrab.grabclipboard()
            if isinstance(initial, Image.Image):
                self.last_hash = hashlib.md5(initial.tobytes()).hexdigest()
        except Exception:
            pass

    def check_and_capture_now(self) -> Optional[Path]:
        """Manually grab whatever image is currently in the clipboard right now."""
        try:
            img = ImageGrab.grabclipboard()
            if isinstance(img, Image.Image):
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
                snip_path = self.drop_dir / f"snip_{ts}.png"
                img.convert("RGB").save(snip_path, "PNG")
                return snip_path
        except Exception:
            pass
        return None

    def run(self):
        while self.running:
            try:
                img = ImageGrab.grabclipboard()
                if isinstance(img, Image.Image):
                    raw_bytes = img.tobytes()
                    img_hash = hashlib.md5(raw_bytes).hexdigest()
                    if img_hash != self.last_hash:
                        self.last_hash = img_hash
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
                        snip_path = self.drop_dir / f"snip_{ts}.png"
                        img.convert("RGB").save(snip_path, "PNG")
                        threading.Thread(target=self.processor.process, args=(snip_path,), daemon=True).start()
            except Exception:
                pass
            time.sleep(0.5)

    def stop(self):
        self.running = False

class ScreenshotWatcher:
    """Orchestrates filesystem observers, clipboard monitor, and triage surfacing."""
    def __init__(self, watch_dirs: List[Path] = WATCH_DIRS, on_new_capture: Optional[Callable] = None):
        self.watch_dirs = [Path(d) for d in watch_dirs] if watch_dirs else [FALLBACK_WATCH_DIR]
        self.on_new_capture = on_new_capture
        self.processor = ScreenshotProcessor(on_new_capture=self.on_new_capture)
        self.observer = Observer()
        self.handler = ScreenshotEventHandler(self.processor)
        self.clipboard_watcher = ClipboardWatcher(self.processor, drop_dir=FALLBACK_WATCH_DIR)

    def start(self):
        watched_count = 0
        for directory in self.watch_dirs:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                self.observer.schedule(self.handler, path=str(directory), recursive=False)
                watched_count += 1
            except Exception as e:
                print(f"[Watcher Notice] Skipping {directory}: {e}")

        if watched_count > 0:
            self.observer.start()

        self.clipboard_watcher.start()
        print("[*] Quiet background monitor active (Watching filesystem + clipboard)")

    def grab_clipboard_now(self) -> Optional[dict]:
        """Manually trigger capture from current clipboard contents."""
        path = self.clipboard_watcher.check_and_capture_now()
        if path:
            return self.processor.process(path)
        return None

    def stop(self):
        self.clipboard_watcher.stop()
        try:
            self.observer.stop()
            self.observer.join(timeout=2.0)
        except Exception:
            pass
