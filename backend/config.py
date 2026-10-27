"""All runtime configuration comes from environment variables (or .env)."""
import json
import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

# ---- App -------------------------------------------------------------------
APP_NAME = env("APP_NAME", "Chat3D")
APP_URL = env("APP_URL", "http://localhost:8000").rstrip("/")
ENVIRONMENT = env("ENVIRONMENT", "development")  # development | production
IS_PROD = ENVIRONMENT == "production"
SECRET_KEY = env("SECRET_KEY") or ("dev-only-" + secrets.token_hex(16))
if IS_PROD and (len(env("SECRET_KEY")) < 32 or env("SECRET_KEY").startswith("#")):
    raise RuntimeError("SECRET_KEY must be set to 32+ random characters in production")
COOKIE_SECURE = env_bool("COOKIE_SECURE", APP_URL.startswith("https://"))
SESSION_DAYS = env_int("SESSION_DAYS", 30)
ADMIN_EMAILS = {e.strip().lower() for e in env("ADMIN_EMAILS").split(",") if e.strip()}

# ---- Business details (legal pages, receipts) ------------------------------
COMPANY_NAME = env("COMPANY_NAME", "MeeraAI Tech Solutions LLP")
COMPANY_ADDRESS = env(
    "COMPANY_ADDRESS", "2nd Floor, A Wing 202, Rizvi Palace, Hill Road, Bandra (West), Mumbai 400050, India"
)
SUPPORT_EMAIL = env("SUPPORT_EMAIL", "team@meeraaitech.com")
GOVERNING_LAW = env("GOVERNING_LAW", "the laws of India, with courts in Mumbai, Maharashtra")

# ---- Database --------------------------------------------------------------
DATABASE_URL = env("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'chat3d.db'}")

# ---- Storage ---------------------------------------------------------------
STORAGE_BACKEND = env("STORAGE_BACKEND", "local")  # local | s3
LOCAL_STORAGE_DIR = Path(env("LOCAL_STORAGE_DIR", str(ROOT / "data" / "files")))
S3_BUCKET = env("S3_BUCKET")
S3_REGION = env("S3_REGION", "ap-south-1")
S3_PREFIX = env("S3_PREFIX", "chat3d/")
SIGNED_URL_TTL = env_int("SIGNED_URL_TTL", 3600)

# ---- Providers -------------------------------------------------------------
FAL_KEY = env("FAL_KEY") or None
HF_TOKEN = env("HF_TOKEN") or None
# fal (production) | mock (offline development)
IMAGE_PROVIDER = env("IMAGE_PROVIDER", "fal")
BG_PROVIDER = env("BG_PROVIDER", "fal")  # fal (BiRefNet, MIT) | none | mock
MESH_PROVIDER = env("MESH_PROVIDER", "fal")  # fal (TRELLIS.2) | hf_space (dev only) | mock
FAL_IMAGE_MODEL = env("FAL_IMAGE_MODEL", "fal-ai/flux/schnell")
FAL_BG_MODEL = env("FAL_BG_MODEL", "fal-ai/birefnet/v2")
FAL_MESH_MODEL = env("FAL_MESH_MODEL", "fal-ai/trellis-2")
HF_TRELLIS_SPACE = env("HF_TRELLIS_SPACE", "microsoft/TRELLIS.2")
MOCK_DELAY = float(env("MOCK_DELAY", "1.0"))

# ---- Credits & limits ------------------------------------------------------
SIGNUP_CREDITS = env_int("SIGNUP_CREDITS", 12)
COST_IMAGE = env_int("COST_IMAGE", 1)
COST_MODEL = {
    "512": env_int("COST_MODEL_512", 8),
    "1024": env_int("COST_MODEL_1024", 10),
    "1536": env_int("COST_MODEL_1536", 12),
}
MAX_ACTIVE_JOBS_PER_USER = env_int("MAX_ACTIVE_JOBS_PER_USER", 2)
WORKER_THREADS = env_int("WORKER_THREADS", 8)
MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 15)

# Credit packs. Override with CREDIT_PACKS='[{"id":..., "credits":..., "inr":..., "usd":...}]'
# Prices are in the smallest unit: paise for INR, cents for USD.
DEFAULT_PACKS = [
    {"id": "starter", "name": "Starter", "credits": 50, "inr": 49900, "usd": 699},
    {"id": "creator", "name": "Creator", "credits": 200, "inr": 169900, "usd": 2399, "popular": True},
    {"id": "studio", "name": "Studio", "credits": 600, "inr": 449900, "usd": 6399},
]
CREDIT_PACKS = json.loads(env("CREDIT_PACKS")) if env("CREDIT_PACKS") else DEFAULT_PACKS

# ---- Payments --------------------------------------------------------------
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET")
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = env("STRIPE_WEBHOOK_SECRET")

# ---- Email (password reset, receipts) --------------------------------------
SMTP_HOST = env("SMTP_HOST")
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_USER = env("SMTP_USER")
SMTP_PASSWORD = env("SMTP_PASSWORD")
SMTP_FROM = env("SMTP_FROM", f"{APP_NAME} <{SUPPORT_EMAIL}>")


def public_config() -> dict:
    return {
        "app_name": APP_NAME,
        "company": COMPANY_NAME,
        "support_email": SUPPORT_EMAIL,
        "signup_credits": SIGNUP_CREDITS,
        "cost_image": COST_IMAGE,
        "cost_model": COST_MODEL,
        "packs": CREDIT_PACKS,
        "razorpay": bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET),
        "razorpay_key_id": RAZORPAY_KEY_ID or None,
        "stripe": bool(STRIPE_SECRET_KEY),
        "test_mode": IMAGE_PROVIDER == "mock" or MESH_PROVIDER == "mock",
    }
