"""Expose only the template globe; never start personal backup/restore services."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from app import auth, demoseed
from app.main import app as globe_app

@asynccontextmanager
async def lifespan(app: FastAPI):
    demoseed.reset_demo_globe()
    auth.reload_secret()
    yield

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

@app.middleware("http")
async def template_only(request: Request, call_next):
    path = request.url.path
    if path == "/":
        return RedirectResponse("/demo/", status_code=307)
    if path != "/demo" and not path.startswith("/demo/"):
        return JSONResponse({"detail": "Template globe only"}, status_code=404)
    return await call_next(request)

app.mount("/", globe_app)
