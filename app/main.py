import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .routes import auth, history, home, ingest
from .timing import timing_middleware

STATIC = Path(__file__).parent / "static"

logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="Peak member dashboard")

app.middleware("http")(timing_middleware)

app.include_router(auth.router)
app.include_router(home.router)
app.include_router(history.router)
app.include_router(ingest.router)

# The UI is plain HTML, CSS and JavaScript with no build step, served by this
# app. One process, one port, no CORS.
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
