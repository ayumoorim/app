import re
import os
from pathlib import Path
from PIL import Image
from app.config import TESSERACT_CMD, TESSERACT_FOUND

# Setup pytesseract with robust verification and fallback handling
HAVE_TESSERACT = False
TESSERACT_VERSION = None

try:
    import pytesseract
    if TESSERACT_CMD and os.path.isfile(TESSERACT_CMD):
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
        try:
            TESSERACT_VERSION = str(pytesseract.get_tesseract_version())
            HAVE_TESSERACT = True
            print(f"[OCR] Tesseract OCR v{TESSERACT_VERSION} initialized successfully at: {TESSERACT_CMD}")
        except Exception as e:
            print(f"[OCR Warning] Tesseract binary exists at {TESSERACT_CMD} but execution failed: {e}")
            HAVE_TESSERACT = False
    else:
        HAVE_TESSERACT = False
except Exception as e:
    HAVE_TESSERACT = False
    print(f"[OCR Warning] pytesseract module error: {e}")

# Heuristic Keyword Dictionaries with weights for classification
KEYWORD_RULES = {
    "Code": {
        "keywords": [
            r"\bdef\b", r"def\s+", r"\bclass\b", r"class\s+", r"\bimport\b", r"import\s+",
            r"\bfrom\b", r"\breturn\b", r"return\s+", r"\bfunction\b", r"\bconst\b",
            r"\blet\b", r"\bvar\b", r"\bconsole\.log\b", r"\bprint\s*\(", r"\bpublic\b", r"\bprivate\b",
            r"\bvoid\b", r"\bstring\b", r"\bint\b", r"\bboolean\b", r"\bSELECT\b", r"\bFROM\b", r"\bWHERE\b",
            r"\bINSERT\b", r"\bUPDATE\b", r"\b<div>\b", r"\b<\/div>\b", r"\bjsx\b",
            r"\bcss\b", r"\bhtml\b", r"[{};=>]{2,}", r"\bTraceback\b", r"\bError:\b",
            r"\bException\b", r"\bthreading\b", r"\bThread\b", r"\bdaemon\b", r"\basync\b",
            r"\bawait\b", r"\bendpoint\b", r"\bapi\b", r"\bfastapi\b", r"\buvicorn\b",
            r"\bwatchdog\b", r"\bself\.\w+", r"\b[a-zA-Z_]\w*\s*=\s*", r"\b[a-zA-Z_]\w*\.\w+\(",
            r"#\s*[a-zA-Z0-9]+", r"//\s*[a-zA-Z0-9]+", r"\bpython\b", r"\bpip\b",
            r"\bnpm\b", r"\bgit\b", r"C:\\", r"\bmain\.py\b", r"\.py\b", r"\.js\b", r"\.ts\b"
        ],
        "weight": 2.8
    },
    "Chats": {
        "keywords": [
            r"\btyping\.\.\.\b", r"\bonline\b", r"\blast seen\b", r"\bmessage\b",
            r"\bdelivered\b", r"\bread\b", r"\bsent\b", r"\breply\b",
            r"\b\d{1,2}:\d{2}\s*(?:am|pm|AM|PM)\b", r"\bvoice note\b",
            r"\bwhatsapp\b", r"\bslack\b", r"\bdiscord\b", r"\btelegram\b",
            r"\byesterday\b", r"\btoday\b", r"\bunread\b"
        ],
        "weight": 2.0
    },
    "Documents": {
        "keywords": [
            r"\binvoice\b", r"\breceipt\b", r"\bbill to\b", r"\bamount due\b",
            r"\bsubtotal\b", r"\btax\b", r"\btotal\b", r"\bstatement\b",
            r"\bagreement\b", r"\bcontract\b", r"\bterms and conditions\b",
            r"\bpolicy\b", r"\bpage \d+\b", r"\bconfidential\b",
            r"\bdear\s+(?:mr|ms|mrs|sir|madam)\b", r"\bsincerely\b", r"\breference number\b",
            r"\bbalance due\b", r"\bpayment terms\b"
        ],
        "weight": 2.2
    },
    "References": {
        "keywords": [
            r"https?:\/\/[^\s]+", r"\bwww\.[^\s]+", r"\bchapter\b",
            r"\bfigure\b", r"\btable\b", r"\bdiagram\b", r"\bsource:\b",
            r"\bdefinition\b", r"\bwikipedia\b", r"\barticle\b"
        ],
        "weight": 1.5
    }
}

def extract_text(image_path: Path) -> str:
    """Extract text from an image using Pytesseract OCR with safe fallback."""
    if not HAVE_TESSERACT:
        return "[OCR notice: Tesseract not configured. Using heuristic categorization.]"
    
    try:
        with Image.open(image_path) as img:
            rgb_img = img.convert("RGB")
            text = pytesseract.image_to_string(rgb_img)
            return text.strip()
    except Exception as e:
        return f"[OCR extraction error: {str(e)}]"

def classify_text(text: str) -> tuple[str, float]:
    """
    Classify extracted text into one of: 'Code', 'Documents', 'Chats', 'References'.
    Returns (category_name, confidence_score).
    """
    if not text or text.startswith("[OCR"):
        return "References", 0.5

    scores = {category: 0.0 for category in KEYWORD_RULES}
    lowered = text.lower()

    for category, config in KEYWORD_RULES.items():
        weight = config["weight"]
        for pattern in config["keywords"]:
            matches = len(re.findall(pattern, lowered, flags=re.IGNORECASE))
            if matches > 0:
                scores[category] += matches * weight

    best_category = max(scores, key=scores.get)
    max_score = scores[best_category]

    if max_score == 0:
        return "References", 0.4

    total_score = sum(scores.values())
    confidence = round(min(0.99, max_score / (total_score + 1.0)), 2)
    return best_category, confidence
