import os
import sys
import shutil
from pathlib import Path

# Base Paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# User Home & Directories
USER_HOME = Path.home()
FALLBACK_WATCH_DIR = PROJECT_ROOT / "incoming_screenshots"
FALLBACK_WATCH_DIR.mkdir(parents=True, exist_ok=True)

def _is_accessible_dir(p: Path) -> bool:
    """Safely checks if a directory exists and is accessible without crashing."""
    try:
        return p.is_dir()
    except Exception:
        return False

def discover_all_screenshot_dirs() -> list[Path]:
    r"""
    Dynamically discovers all valid screenshot directories on this machine:
    1. Query Windows Registry for configured Screenshots and Pictures paths
    2. Check custom OneDrive Desktop locations (e.g. C:\One Drive Desktop\Pictures\Pictures\Screenshots)
    3. Check standard Windows and User profile paths
    4. Project-local incoming_screenshots
    """
    discovered: list[Path] = []

    # 1. Check environment variable override
    env_dir = os.getenv("SCREENSHOT_WATCH_DIR")
    if env_dir:
        p = Path(env_dir)
        if _is_accessible_dir(p) and p not in discovered:
            discovered.append(p)

    # 2. Query Windows Registry for user's official shell folders
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        )
        registry_keys = [
            "{B7BEDE81-DF94-4682-A7D8-57A52620B86F}",  # Windows Screenshots GUID
            "My Pictures",
            "{0DDD015D-B06C-45D5-8C4C-F59713854639}"
        ]
        for val_name in registry_keys:
            try:
                val, _ = winreg.QueryValueEx(key, val_name)
                expanded = os.path.expandvars(val)
                p = Path(expanded)
                # Check the path directly and standard Screenshots subfolders
                for candidate in [p, p / "Screenshots", p / "Pictures" / "Screenshots"]:
                    if _is_accessible_dir(candidate) and candidate not in discovered:
                        discovered.append(candidate)
            except Exception:
                pass
    except Exception:
        pass

    # 3. Known system locations common on Windows machines with OneDrive
    known_candidates = [
        Path(r"C:\One Drive Desktop\Pictures\Pictures\Screenshots"),
        Path(r"C:\One Drive Desktop\Pictures\Screenshots"),
        Path(r"C:\One Drive Desktop\Desktop"),
        Path(r"C:\Users\Ayush Sharma\Pictures\Screenshots"),
        Path(r"C:\Users\Ayush Sharma\OneDrive\Pictures\Screenshots"),
        USER_HOME / "Pictures" / "Screenshots",
        USER_HOME / "OneDrive" / "Pictures" / "Screenshots",
        USER_HOME / "Desktop"
    ]

    for candidate in known_candidates:
        if _is_accessible_dir(candidate) and candidate not in discovered:
            discovered.append(candidate)

    # 4. Always include project fallback for clipboard and manual drops
    if FALLBACK_WATCH_DIR not in discovered:
        discovered.append(FALLBACK_WATCH_DIR)

    return discovered

WATCH_DIRS = discover_all_screenshot_dirs()
WATCH_DIR = WATCH_DIRS[0] if WATCH_DIRS else FALLBACK_WATCH_DIR

STORAGE_DIR = PROJECT_ROOT / "storage"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

CATEGORIES = ["Code", "Documents", "Chats", "References"]
for cat in CATEGORIES:
    (STORAGE_DIR / cat).mkdir(parents=True, exist_ok=True)

def resolve_tesseract_cmd() -> tuple[str, bool]:
    """Auto-detects Tesseract and injects directory into os.environ['PATH']."""
    env_path = os.getenv("TESSERACT_PATH")
    if env_path and os.path.isfile(env_path):
        _inject_tesseract_environment(Path(env_path))
        return env_path, True

    known_paths = [
        Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
        Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        USER_HOME / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe",
        USER_HOME / "AppData" / "Local" / "Tesseract-OCR" / "tesseract.exe"
    ]

    for path in known_paths:
        try:
            if path.is_file():
                _inject_tesseract_environment(path)
                return str(path), True
        except Exception:
            continue

    system_tess = shutil.which("tesseract")
    if system_tess:
        try:
            if os.path.isfile(system_tess):
                _inject_tesseract_environment(Path(system_tess))
                return system_tess, True
        except Exception:
            pass

    fallback_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return str(fallback_path), fallback_path.is_file()

def _inject_tesseract_environment(executable_path: Path):
    try:
        tess_dir = str(executable_path.parent)
        current_path = os.environ.get("PATH", "")
        if tess_dir not in current_path:
            os.environ["PATH"] = tess_dir + os.pathsep + current_path

        tessdata_dir = executable_path.parent / "tessdata"
        if tessdata_dir.is_dir() and "TESSDATA_PREFIX" not in os.environ:
            os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
    except Exception:
        pass

TESSERACT_CMD, TESSERACT_FOUND = resolve_tesseract_cmd()

# Server Configuration
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
