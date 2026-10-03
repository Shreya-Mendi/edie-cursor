"""Local server: serves the mock forms, the replay data, and the model calls.

Run: .venv/bin/uvicorn server.app:app --port 8000
Everything runs on this machine. No API keys, no network calls.
"""
import hashlib
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vc.asr import WhisperASR
from vc.data import load_encounters
from vc.fill import load_filler
from vc.forms import FORMS
from vc.router import EmbeddingRouter
from vc.schema import ROOT, load_fields, public_schema

app = FastAPI(title="edie-cursor (local)")
router = EmbeddingRouter()
filler = load_filler()
asr = WhisperASR.load_if_present()


@app.middleware("http")
async def revalidate(request, call_next):
    """Dev server: make browsers re-check files every load (cheap 304s), so edits always show."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/mock", StaticFiles(directory=ROOT / "mock"), name="mock")
app.mount("/ext", StaticFiles(directory=ROOT / "extension"), name="ext")


@app.get("/dev/{form}", response_class=HTMLResponse)
def dev_page(form: str):
    """A mock form with the content script injected, for testing without installing the extension."""
    if form not in FORMS:
        raise HTTPException(404, f"no form {form}; try one of {list(FORMS)}")
    page = (ROOT / "mock" / FORMS[form]["page"]).read_text().replace("<head>", '<head>\n<base href="/mock/">', 1)
    return page.replace("</body>", '<script src="/ext/content/agent.js"></script>\n</body>')


@app.get("/api/health")
def health():
    return {"router": router.name, "filler": filler.name if filler else None, "asr": asr.name if asr else None,
            "forms": list(FORMS)}


@app.post("/api/transcribe")
async def transcribe(request: Request):
    """One recorded utterance in (raw audio bytes, e.g. audio/webm), text out."""
    if asr is None:
        raise HTTPException(503, "speech model not installed")
    audio = await request.body()
    if len(audio) < 1000:
        return {"text": "", "ms": 0}
    ctype = request.headers.get("content-type", "")
    suffix = ".wav" if "wav" in ctype else ".mp4" if "mp4" in ctype else ".webm"
    return asr.transcribe(audio, suffix=suffix)


@app.get("/api/schema")
def schema(form: str = "oasis"):
    """A form's fields as Python builds them, to check the page reader against."""
    return public_schema(load_fields(form))


@app.get("/api/encounters")
def encounters(form: Optional[str] = None):
    forms = [form] if form else list(FORMS)
    return [{"id": e["id"], "title": e["title"], "visit": e["visit"], "turns": len(e["turns"]), "form": f}
            for f in forms for e in load_encounters(f)]


@app.get("/api/encounters/{enc_id}")
def encounter(enc_id: str):
    """Turns for replay. Gold answers and route labels are withheld from the client."""
    for f in FORMS:
        for e in load_encounters(f):
            if e["id"] == enc_id:
                turns = [{"who": t["who"], "role": e["speakers"][t["who"]], "text": t["text"]} for t in e["turns"]]
                return {"id": e["id"], "title": e["title"], "visit": e["visit"], "turns": turns, "form": f}
    raise HTTPException(404, f"no encounter {enc_id}")


class RouteReq(BaseModel):
    fields: list[dict]
    utterance: str
    current: Optional[str] = None


@app.post("/api/route")
def route(req: RouteReq):
    return router.route(req.fields, req.utterance, req.current)


class FillReq(BaseModel):
    field: dict
    turns: list[dict]
    visit: Optional[dict] = None
    form: Optional[str] = None


# The filling model is greedy, so the same field and transcript always give the same answer.
# Answers are remembered on disk so "Finish all" can show a whole visit at once in a demo;
# anything not remembered yet is computed live as usual.
FILL_CACHE = ROOT / "data" / "cache" / "fills.json"
_fills = json.loads(FILL_CACHE.read_text()) if FILL_CACHE.exists() else {}


@app.post("/api/fill")
def fill(req: FillReq):
    if filler is None:
        raise HTTPException(503, "local LLM not installed")
    key = hashlib.sha256(json.dumps([req.form, req.field, req.turns, req.visit], sort_keys=True).encode()).hexdigest()
    if key in _fills:
        return {**_fills[key], "ms": 0, "cached": True}
    out = filler.fill(req.field, req.turns, req.visit, req.form)
    _fills[key] = out
    FILL_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FILL_CACHE.write_text(json.dumps(_fills))
    return out
