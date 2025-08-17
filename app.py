import os
import re
import base64
import logging
from datetime import date
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

# ------------------------------
# Load env
# ------------------------------
load_dotenv()
USE_FAKE_TEXT = os.environ.get("USE_FAKE_TEXT", "false").lower() == "true"
FAKE_TEXT_FILE = os.environ.get("FAKE_TEXT_FILE")

# ------------------------------
# Flask & Logging setup
# ------------------------------
app = Flask(__name__, static_folder="static")
CORS(app)
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# ------------------------------
# Google Vision (lazy init)
# ------------------------------
vision_client = None

def get_vision_client():
    """Lazy init Vision client；若未設金鑰則回傳 None 以避免報錯。"""
    global vision_client
    if vision_client is None:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not cred_path or not os.path.exists(cred_path):
            logger.warning("GOOGLE_APPLICATION_CREDENTIALS 未設定或檔案不存在，Vision API 不可用")
            return None
        try:
            from google.cloud import vision
            vision_client = vision.ImageAnnotatorClient()
        except Exception as e:
            logger.error("Vision client 初始化失敗: %s", e)
            return None
    return vision_client

def run_vision_ocr(img_bytes: bytes, lang_hints: list) -> str:
    """呼叫 Vision；若不可用則回傳空字串。"""
    from google.cloud import vision
    from google.cloud.vision_v1 import types
    client = get_vision_client()
    if client is None:
        logger.warning("Vision client 不可用，回傳空字串")
        return ""
    image = vision.Image(content=img_bytes)
    image_context = types.ImageContext(language_hints=lang_hints) if lang_hints else None
    resp = client.document_text_detection(image=image, image_context=image_context)
    if resp.error.message:
        raise RuntimeError(f"Vision API error: {resp.error.message}")
    return resp.full_text_annotation.text if resp.full_text_annotation else ""

def load_fake_text():
    """讀取 fake_text.txt（多行），供測試用。"""
    if FAKE_TEXT_FILE and os.path.exists(FAKE_TEXT_FILE):
        with open(FAKE_TEXT_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return ""

# ------------------------------
# Helpers
# ------------------------------
def read_image_bytes(req):
    """支援 multipart/form-data 的 'image' 或 JSON 的 'image_base64'。"""
    if "image" in req.files:
        return req.files["image"].read()
    data = req.get_json(silent=True) or {}
    b64 = data.get("image_base64")
    if not b64:
        raise ValueError("No image provided")
    if "," in b64 and b64.strip().startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)

def _to_int(s, default=None):
    try:
        return int(s)
    except Exception:
        return default

# ------------------------------
# Parsing rules（日期/場地/標題）
# ------------------------------
VENUE_WORDS = [
    "日本武道館","東京巨蛋","代代木第一體育館","橫濱体育館","大阪城Hall",
    "東急シアターオーブ","東京国際フォーラム","東京ドームシティホール"
]

# 日期樣式：2025年8月10日 / 2025-8-10 / 2025.8.10 / 8月10日 2025年 / 25年8月10日
DATE_REGEXES = [
    re.compile(r'(?P<y>20\d{2})\s*[./年-]\s*(?P<m>\d{1,2})\s*(?:[./月-])\s*(?P<d>\d{1,2})'),
    re.compile(r'(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日(?:\([^)]+\))?\s*(?P<y>20\d{2})\s*年?'),
    re.compile(r'(?P<y2>\d{2})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})'),
]

TITLE_KEYWORDS = [
    "ミュージカル","コンサート","ライブ","公演","レ・ミゼラブル",
    "LES MISERABLES","WORLD TOUR","ワールドツアー","スペクタキュラー","スペクタクル"
]

# 遇到這些行就視為「主辦/附註/票務等非標題」→ 停止拼接
TITLE_STOP_PAT = re.compile(
    r"(主催|招聘|製作|制作|共同制作|企画|協賛|協力|お問い合わせ|お問合せ|問合せ|HP[:：]|https?://|TEL[:：]"
    r"|発券店|払込票|指定席|自由席|席|扉|列|番|消費税込|特定チケット|転売|CNプレイガイド|セブン-イレブン)",
    re.IGNORECASE
)

def _collect_dates(text: str):
    """回傳 (date_obj, index) 清單，用於後續決策。"""
    out = []
    for rgx in DATE_REGEXES:
        for m in rgx.finditer(text):
            gd = m.groupdict()
            if gd.get('y'):
                y = _to_int(gd['y'])
            elif gd.get('y2'):
                y = 2000 + _to_int(gd['y2'], 0)  # 兩位數年份 → 20xx
            else:
                y = None
            M = _to_int(gd.get('m'))
            d = _to_int(gd.get('d'))
            if y and M and d:
                try:
                    out.append((date(y, M, d), m.start()))
                except ValueError:
                    continue
    return out

def parse_datetime(text: str):
    """只決定活動日期：採用『最新日期』以避開購票/發券日。"""
    dates = _collect_dates(text)
    logger.debug("Found dates: %s", [d.strftime("%Y-%m-%d") for d,_ in dates])
    event_date = max((d for d,_ in dates), default=None)
    return {"event_date": event_date.strftime("%Y-%m-%d") if event_date else None}

def parse_venue(text: str):
    for v in VENUE_WORDS:
        if v in text:
            logger.debug("Venue matched: %s", v)
            return v
    return None

def _is_upper_english(s: str) -> bool:
    letters = re.sub(r'[^A-Za-z]', '', s)
    return len(letters) >= 4 and letters.isupper()

def parse_title(text: str):
    """從關鍵詞起始行向後拼接「像標題的行」，遇到主辦/附註等就停止。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    n = len(lines)
    # 找到第一個含關鍵詞的行
    start = -1
    for i, ln in enumerate(lines):
        if any(k in ln for k in TITLE_KEYWORDS):
            start = i
            break
    if start == -1:
        # 備援：第一個全大寫英文行
        for ln in lines:
            if _is_upper_english(ln):
                return ln
        return None

    parts = []
    i = start
    while i < n:
        ln = lines[i]
        if TITLE_STOP_PAT.search(ln):
            break
        # 只接受：包含關鍵詞 / 有日文引號『』/ 或全大寫英文行
        if (any(k in ln for k in TITLE_KEYWORDS)) or ("『" in ln or "』" in ln) or _is_upper_english(ln):
            parts.append(ln)
            # 最多拼 3 行，避免吃到無關內容
            if len(parts) >= 3:
                break
            i += 1
            continue
        # 一遇到不像標題的行就停
        break

    title = " ".join(parts).strip()
    # 壓成單一空白
    title = re.sub(r"\s+", " ", title)
    logger.debug("Title parsed: %s", title)
    return title or None

# ------------------------------
# Routes
# ------------------------------
@app.route("/", methods=["GET"])
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    try:
        if USE_FAKE_TEXT:
            raw_text = load_fake_text()
            if not raw_text:
                raise RuntimeError("FAKE_TEXT_FILE not found or empty")
            logger.debug("Loaded FAKE_TEXT, length=%d", len(raw_text))
        else:
            img_bytes = read_image_bytes(request)
            lang_str = os.environ.get("OCR_LANGUAGE_HINTS", "ja,zh-Hant,en")
            hints = [s.strip() for s in lang_str.split(",") if s.strip()]
            raw_text = run_vision_ocr(img_bytes, hints)
            if not raw_text:
                raw_text = "[No OCR result - Vision client not available]"
            logger.debug("Got raw_text length=%d", len(raw_text))

        # 解析
        fields = parse_datetime(raw_text)
        fields["venue"] = parse_venue(raw_text)
        fields["title"] = parse_title(raw_text)

        # 方便除錯：印出前幾行
        preview = "\n".join(raw_text.splitlines()[:12])
        logger.debug("Raw text preview:\n%s\n--- (truncated)", preview)

        return jsonify({"ok": True, "raw_text": raw_text, "fields": fields}), 200
    except Exception as e:
        logger.exception("Error in /api/ocr")
        return jsonify({"ok": False, "error": str(e)}), 400

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

# ------------------------------
# Entrypoint
# ------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
