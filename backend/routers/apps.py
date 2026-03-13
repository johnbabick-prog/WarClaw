"""
App Factory endpoints — generate, list, view, and delete AI-created apps.
"""
import logging
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..services.app_factory import generate_app, list_generated_apps, get_app_frontend, delete_app
from ..services.llm import llm_service

log = logging.getLogger("warclaw.apps")
router = APIRouter(prefix="/api/apps", tags=["apps"])


class CreateAppRequest(BaseModel):
    name: str
    description: str
    context: str = ""   # Integration context: hosts, protocols, etc.


@router.post("/generate")
async def create_app(req: CreateAppRequest):
    """
    Ask WarClaw AI to generate a full-stack app.
    Returns immediately with a task ID; check /api/apps for the result.
    This endpoint streams generation — may take 30-120s depending on model.
    """
    if not llm_service.ready:
        raise HTTPException(status_code=503, detail="No model loaded. Load a GGUF model first.")

    if not req.name.strip():
        raise HTTPException(status_code=400, detail="App name is required")

    try:
        manifest = await generate_app(req.name, req.description, req.context)
        return manifest
    except Exception as e:
        log.exception("App generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
def list_apps():
    """List all generated apps."""
    return {"apps": list_generated_apps()}


@router.get("/{slug}")
def get_app(slug: str):
    """Return app manifest."""
    apps = {a["slug"]: a for a in list_generated_apps()}
    if slug not in apps:
        raise HTTPException(status_code=404, detail="App not found")
    return apps[slug]


@router.get("/{slug}/ui", response_class=HTMLResponse)
def serve_app_ui(slug: str):
    """Serve the generated app's frontend HTML."""
    html = get_app_frontend(slug)
    if html is None:
        manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
        if manifest_path.exists():
          try:
              manifest = json.loads(manifest_path.read_text())
              detail = "App frontend not found"
              if manifest.get("status") == "partial":
                  detail = f"App generation is partial: {', '.join(manifest.get('errors', []))}"
              raise HTTPException(status_code=404, detail=detail)
          except HTTPException:
              raise
          except Exception:
              pass
        raise HTTPException(status_code=404, detail="App frontend not found")
    return HTMLResponse(content=html)


@router.delete("/{slug}")
def remove_app(slug: str):
    """Delete a generated app."""
    if delete_app(slug):
        return {"status": "deleted", "slug": slug}
    raise HTTPException(status_code=404, detail="App not found")
