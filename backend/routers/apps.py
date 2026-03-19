import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from ..services.app_factory import (
    build_app_data,
    delete_app,
    delete_app_file,
    delete_app_report,
    generate_app,
    get_app_frontend,
    get_app_spec,
    list_app_files,
    list_app_reports,
    list_generated_apps,
    list_app_tasks,
    summarize_app_files,
    iterate_app,
    update_app,
    add_app_task,
    update_app_task,
    delete_app_task,
    _reports_dir,
    _to_markdown_report,
    get_latest_app_report,
    _safe_filename,
    _uploads_dir,
)
from ..services.llm import llm_service
from ..services.mission_log import emit

log = logging.getLogger("warclaw.apps")
router = APIRouter(prefix="/api/apps", tags=["apps"])


class CreateAppRequest(BaseModel):
    name: str
    description: str
    context: str = ""   # Integration context: hosts, protocols, etc.


class AppReportRequest(BaseModel):
    report_type: str = "uploaded_report"
    prompt: str = ""


class AppTaskRequest(BaseModel):
    title: str
    notes: str = ""
    due_label: str = ""


class AppTaskUpdateRequest(BaseModel):
    title: str | None = None
    notes: str | None = None
    due_label: str | None = None
    completed: bool | None = None


class AppIterationRequest(BaseModel):
    instruction: str


@router.post("/generate")
async def create_app(req: CreateAppRequest):
    """
    Ask WarClaw AI to generate a full-stack app.
    Returns immediately with a task ID; check /api/apps for the result.
    This endpoint streams generation — may take 30-120s depending on model.
    """
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="App name is required")

    try:
        manifest = await generate_app(req.name, req.description, req.context)
        return manifest
    except Exception as e:
        log.exception("App generation failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{slug}/update")
async def update_existing_app(slug: str, req: CreateAppRequest):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="App name is required")
    try:
        return await update_app(slug, req.name, req.description, req.context)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="App not found")
    except Exception as e:
        log.exception("App update failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{slug}/iterate")
async def iterate_existing_app(slug: str, req: AppIterationRequest):
    if not req.instruction.strip():
        raise HTTPException(status_code=400, detail="Iteration instruction is required")
    try:
        return await iterate_app(slug, req.instruction)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="App not found")
    except Exception as e:
        log.exception("App iteration failed")
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
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/{slug}/data")
def get_app_data(slug: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    spec = get_app_spec(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail="App spec not found")
    return JSONResponse(content=build_app_data(slug, spec))


@router.get("/{slug}/workspace")
def get_app_workspace(slug: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = get_app_spec(slug)
    if spec is None:
        raise HTTPException(status_code=404, detail="App spec not found")
    return {
        "app": {
            "slug": slug,
            "name": manifest.get("name", slug),
            "description": manifest.get("description", ""),
            "context": manifest.get("context", ""),
            "app_kind": spec.get("app_kind", "operational_dashboard"),
            "summary": spec.get("summary", ""),
        },
        "files": list_app_files(slug),
        "reports": list_app_reports(slug),
        "latest_report": get_latest_app_report(slug),
        "tasks": list_app_tasks(slug),
    }


@router.post("/{slug}/tasks")
def create_app_task(slug: str, req: AppTaskRequest):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Task title is required")
    task = add_app_task(slug, req.title, req.notes, req.due_label)
    emit("info", "app", "Task added to generated app", {"slug": slug, "task_id": task["id"]})
    return {"status": "created", "task": task}


@router.patch("/{slug}/tasks/{task_id}")
def patch_app_task(slug: str, task_id: str, req: AppTaskUpdateRequest):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    try:
        task = update_app_task(slug, task_id, title=req.title, notes=req.notes, due_label=req.due_label, completed=req.completed)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "updated", "task": task}


@router.delete("/{slug}/tasks/{task_id}")
def remove_app_task(slug: str, task_id: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    if not delete_app_task(slug, task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "deleted", "task_id": task_id}


@router.post("/{slug}/tasks/carry-forward")
def carry_forward_tasks(slug: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    tasks = list_app_tasks(slug)
    created = 0
    for task in tasks:
        if task.get("completed"):
            continue
        add_app_task(
            slug,
            task["title"],
            notes=task.get("notes", ""),
            due_label=task.get("due_label") or "Next watch",
            source="carry_forward",
        )
        created += 1
    return {"status": "created", "created": created}


@router.post("/{slug}/files")
async def upload_app_files(slug: str, files: list[UploadFile] = File(...)):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    uploads_dir = _uploads_dir(slug)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    for upload in files:
        filename = _safe_filename(upload.filename or "")
        dest = uploads_dir / filename
        content = await upload.read()
        dest.write_bytes(content)
        saved.append({
            "name": filename,
            "size_bytes": len(content),
        })
        await upload.close()

    emit("success", "app", f"Uploaded {len(saved)} file(s) to generated app", {"slug": slug, "files": [item["name"] for item in saved]})
    return {"status": "uploaded", "saved": saved}


@router.delete("/{slug}/files/{name}")
def remove_app_file(slug: str, name: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    if not delete_app_file(slug, name):
        raise HTTPException(status_code=404, detail="File not found")
    emit("info", "app", f"Deleted uploaded file from generated app", {"slug": slug, "file": name})
    return {"status": "deleted", "name": Path(name).name}


@router.post("/{slug}/actions/report-summary")
async def run_app_report_summary(slug: str, req: AppReportRequest):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    try:
        result = await summarize_app_files(slug, prompt=req.prompt, report_type=req.report_type)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        log.exception("Generated app report action failed")
        raise HTTPException(status_code=500, detail=str(exc))

    emit("success", "app", f"Generated in-app report for {slug}", {"slug": slug, "report_type": req.report_type})
    return result


@router.get("/{slug}/reports")
def get_app_reports(slug: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    return {"reports": list_app_reports(slug), "latest_report": get_latest_app_report(slug)}


@router.get("/{slug}/reports/{report_name}.md", response_class=PlainTextResponse)
def download_app_report_markdown(slug: str, report_name: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")

    if report_name == "latest":
        payload = get_latest_app_report(slug)
        if payload is None:
            raise HTTPException(status_code=404, detail="No saved report found")
        markdown = _to_markdown_report(payload)
        return PlainTextResponse(content=markdown, headers={"Content-Disposition": f'attachment; filename="{slug}-latest-report.md"'})

    report_path = _reports_dir(slug) / f"{report_name}.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    markdown = _to_markdown_report(payload)
    return PlainTextResponse(content=markdown, headers={"Content-Disposition": f'attachment; filename="{report_name}.md"'})


@router.delete("/{slug}/reports/{report_id}")
def remove_app_report(slug: str, report_id: str):
    manifest_path = Path(__file__).resolve().parents[2] / "generated_apps" / slug / "manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail="App not found")
    if not delete_app_report(slug, report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    emit("info", "app", f"Deleted saved report from generated app", {"slug": slug, "report_id": report_id})
    return {"status": "deleted", "report_id": Path(report_id).name}


@router.get("/{slug}/source")
def list_source_files(slug: str):
    """List editable source files for a generated app."""
    app_dir = Path(__file__).resolve().parents[2] / "generated_apps" / slug
    if not app_dir.exists():
        raise HTTPException(status_code=404, detail="App not found")
    editable = []
    for name in ("index.html", "backend.py", "manifest.json", "spec.json"):
        fp = app_dir / name
        if fp.exists():
            editable.append({
                "name": name,
                "size_bytes": fp.stat().st_size,
                "updated_at": fp.stat().st_mtime,
            })
    return {"files": editable}


@router.get("/{slug}/source/{filename}")
def read_source_file(slug: str, filename: str):
    """Read a source file's content for editing."""
    allowed = {"index.html", "backend.py", "manifest.json", "spec.json"}
    if filename not in allowed:
        raise HTTPException(status_code=400, detail=f"File not editable: {filename}")
    fp = Path(__file__).resolve().parents[2] / "generated_apps" / slug / filename
    if not fp.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return {"name": filename, "content": fp.read_text(encoding="utf-8")}


class SourceFileUpdate(BaseModel):
    content: str


@router.put("/{slug}/source/{filename}")
def write_source_file(slug: str, filename: str, req: SourceFileUpdate):
    """Write updated content to a source file."""
    allowed = {"index.html", "backend.py", "manifest.json", "spec.json"}
    if filename not in allowed:
        raise HTTPException(status_code=400, detail=f"File not editable: {filename}")
    fp = Path(__file__).resolve().parents[2] / "generated_apps" / slug / filename
    if not fp.parent.exists():
        raise HTTPException(status_code=404, detail="App not found")
    fp.write_text(req.content, encoding="utf-8")
    emit("info", "app", f"Source file updated: {slug}/{filename}", {"slug": slug, "file": filename})
    return {"status": "saved", "name": filename, "size_bytes": len(req.content.encode("utf-8"))}


@router.delete("/{slug}")
def remove_app(slug: str):
    """Delete a generated app."""
    if delete_app(slug):
        return {"status": "deleted", "slug": slug}
    raise HTTPException(status_code=404, detail="App not found")
