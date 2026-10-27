"""Chat3D — commercial idea → image → 3D service."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import init_db
from .generations import recover_interrupted
from .routers import admin, auth, billing, keys, pages, public_v1, studio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("chat3d")

app = FastAPI(
    title=f"{config.APP_NAME} API",
    version="2.0",
    description="Turn ideas and images into textured 3D models. Authenticate public endpoints with "
                "`Authorization: Bearer c3d_live_...` (create keys in your account).",
    docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
)


@app.on_event("startup")
def startup():
    init_db()
    n = recover_interrupted()
    if n:
        log.warning("Refunded %d generations interrupted by the last shutdown", n)
    log.info("Providers: image=%s bg=%s mesh=%s storage=%s", config.IMAGE_PROVIDER, config.BG_PROVIDER,
             config.MESH_PROVIDER, config.STORAGE_BACKEND)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    if config.COOKIE_SECURE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return JSONResponse(status_code=422, content={"detail": f"{field}: {first.get('msg', 'invalid value')}"})


@app.get("/api/health", tags=["meta"])
def health():
    return {"ok": True}


for r in (auth.router, studio.router, billing.router, keys.router, public_v1.router, admin.router, pages.router):
    app.include_router(r)
app.mount("/static", StaticFiles(directory=config.FRONTEND), name="static")
