"""
App Factory — AI generates full-stack apps and registers them as live routes.

Generated apps consist of:
  - A Python FastAPI router (backend.py)
  - An HTML/JS/CSS frontend (index.html)
  - Metadata (manifest.json)

Apps are stored under generated_apps/<app_slug>/ and auto-mounted.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from ..config import GENERATED_APPS_DIR
from .llm import llm_service

log = logging.getLogger("warclaw.factory")

BACKEND_GENERATION_PROMPT = """Write a complete Python FastAPI router file. Output ONLY the Python code, nothing else. No explanation, no markdown.

The code must:
- Start with import statements
- Define `router = APIRouter(prefix="/apps/{slug}")`
- Include at least one GET endpoint that returns useful data
- Be fully self-contained using only stdlib + fastapi + pydantic
- Include error handling for network timeouts

App name: {app_name}
Description: {description}
Context: {context}

Begin the Python code now:"""

FRONTEND_GENERATION_PROMPT = """Write a complete HTML file. Output ONLY the HTML, nothing else. No explanation, no markdown.

The HTML must:
- Start with <!DOCTYPE html>
- Be a single self-contained file with embedded <style> and <script> tags
- Use a dark naval theme (background: #0a1628, text: #e8e4dc, accent: #ff6f3b)
- Fetch data from /apps/{slug}/api/ endpoints using JavaScript fetch()
- Have a header showing the app name
- NO external CDN or stylesheet links

App name: {app_name}
Description: {description}
Context: {context}

Begin the HTML now:"""


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40]


def _strip_fences(code: Optional[str]) -> Optional[str]:
    """Remove markdown code fences from LLM output."""
    if not code:
        return code
    code = code.strip()
    # Remove opening fence with optional language tag
    code = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", code)
    # Remove closing fence
    code = re.sub(r"\n?```\s*$", "", code)
    return code.strip()


def _extract_backend(response: str) -> Optional[str]:
    """Extract Python code from LLM response. Tries fenced blocks first, then raw detection."""
    if not response or not response.strip():
        log.warning("Backend response is empty")
        return None

    # Try fenced code blocks first
    fenced_blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
    for code in fenced_blocks:
        code = code.strip()
        if "import" in code or "router" in code:
            return code

    # Try to find raw Python starting from an import statement
    stripped = _strip_fences(response)
    import_match = re.search(
        r"((?:from\s+\w|import\s+\w)[\s\S]*)",
        stripped or "", re.DOTALL
    )
    if import_match:
        code = import_match.group(1).strip()
        if code:
            return code

    # Last resort — return the whole stripped response if it looks like Python
    if stripped and ("import" in stripped or "def " in stripped):
        return stripped

    log.warning("Could not extract backend code from response (%d chars)", len(response))
    return None


def _extract_frontend(response: str) -> Optional[str]:
    """Extract HTML from LLM response. Tries fenced blocks first, then raw detection."""
    if not response or not response.strip():
        log.warning("Frontend response is empty")
        return None

    # Try fenced code blocks first
    fenced_blocks = re.findall(r"```(?:html?)?\s*\n(.*?)```", response, re.DOTALL | re.IGNORECASE)
    for code in fenced_blocks:
        code = code.strip()
        if "<" in code:
            return code

    # Try to find raw HTML starting from DOCTYPE or <html
    stripped = _strip_fences(response)
    html_match = re.search(r"(<!DOCTYPE\s+html[\s\S]*)", stripped or "", re.IGNORECASE)
    if html_match:
        return html_match.group(1).strip()

    html_match = re.search(r"(<html[\s\S]*</html>)", stripped or "", re.DOTALL | re.IGNORECASE)
    if html_match:
        return html_match.group(1).strip()

    # Last resort — if response contains HTML tags, use it
    if stripped and "<" in stripped and ">" in stripped:
        return stripped

    log.warning("Could not extract frontend HTML from response (%d chars)", len(response))
    return None


async def _collect_llm_response(prompt: str, max_tokens: int, temperature: float) -> str:
    response_parts = []
    async for token in llm_service.astream_chat([], prompt, max_tokens=max_tokens, temperature=temperature):
        response_parts.append(token)
    return "".join(response_parts).strip()


async def generate_app(app_name: str, description: str, context: str = "") -> dict:
    """
    Ask the LLM to generate a full-stack app and save it to disk.
    Returns metadata dict with slug, paths, and status.
    """
    slug = _slugify(app_name)
    # Avoid overwriting an existing app — append a short timestamp suffix
    app_dir = GENERATED_APPS_DIR / slug
    if app_dir.exists():
        slug = f"{slug}-{int(time.time()) % 10000}"
        app_dir = GENERATED_APPS_DIR / slug
    app_dir.mkdir(parents=True, exist_ok=True)

    backend_prompt = BACKEND_GENERATION_PROMPT.format(
        app_name=app_name,
        description=description,
        context=context or "No specific integration context provided.",
        slug=slug,
    )
    frontend_prompt = FRONTEND_GENERATION_PROMPT.format(
        app_name=app_name,
        description=description,
        context=context or "No specific integration context provided.",
        slug=slug,
    )

    log.info("Generating app '%s' (slug: %s)", app_name, slug)
    t0 = time.time()

    # Run sequentially — llama-cpp-python is NOT thread-safe for concurrent inference
    log.info("Generating backend for '%s'...", slug)
    backend_response = await _collect_llm_response(backend_prompt, max_tokens=3072, temperature=0.15)
    log.info("Backend response: %d chars", len(backend_response))

    log.info("Generating frontend for '%s'...", slug)
    frontend_response = await _collect_llm_response(frontend_prompt, max_tokens=3072, temperature=0.15)
    log.info("Frontend response: %d chars", len(frontend_response))

    # Save raw responses for debugging
    (app_dir / "raw_backend_response.txt").write_text(backend_response, encoding="utf-8")
    (app_dir / "raw_frontend_response.txt").write_text(frontend_response, encoding="utf-8")

    backend_code = _extract_backend(backend_response)
    frontend_html = _extract_frontend(frontend_response)

    status = "success"
    errors = []

    # Save backend if it looks like valid Python
    if backend_code and ("import" in backend_code or "router" in backend_code or "def " in backend_code):
        # Ensure the code has a router definition; add one if missing
        if "router" not in backend_code:
            backend_code = "from fastapi import APIRouter\n\nrouter = APIRouter(prefix=\"/apps/" + slug + "\")\n\n" + backend_code
        (app_dir / "backend.py").write_text(backend_code, encoding="utf-8")
    else:
        errors.append("Failed to extract backend code")
        status = "partial"
        log.warning("Backend extraction failed for '%s'", slug)

    # Save frontend if it contains any HTML
    if frontend_html and ("<" in frontend_html):
        # Ensure it's a complete HTML document
        if "<html" not in frontend_html.lower():
            frontend_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{app_name} — WarClaw</title>
<style>
body {{ background: #0a1628; color: #e8e4dc; font-family: sans-serif; margin: 0; padding: 20px; }}
h1 {{ color: #ff6f3b; }}
</style>
</head>
<body>
<h1>{app_name}</h1>
{frontend_html}
</body>
</html>"""
        (app_dir / "index.html").write_text(frontend_html, encoding="utf-8")
    else:
        errors.append("Failed to extract frontend HTML")
        status = "partial"
        log.warning("Frontend extraction failed for '%s'", slug)

    manifest = {
        "slug": slug,
        "name": app_name,
        "description": description,
        "context": context,
        "created_at": time.time(),
        "generation_time_s": round(time.time() - t0, 1),
        "status": status,
        "errors": errors,
        "has_backend": backend_code is not None,
        "has_frontend": frontend_html is not None and "<" in (frontend_html or ""),
    }
    (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    log.info("App '%s' generated in %.1fs — status: %s", slug, manifest["generation_time_s"], status)
    return manifest


def list_generated_apps() -> list[dict]:
    """Return manifests of all generated apps."""
    apps = []
    for manifest_file in sorted(GENERATED_APPS_DIR.glob("*/manifest.json")):
        try:
            apps.append(json.loads(manifest_file.read_text()))
        except Exception:
            pass
    return apps


def get_app_frontend(slug: str) -> Optional[str]:
    """Return frontend HTML for a generated app."""
    html_file = GENERATED_APPS_DIR / slug / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return None


def delete_app(slug: str) -> bool:
    """Remove a generated app from disk."""
    import shutil
    app_dir = GENERATED_APPS_DIR / slug
    if app_dir.exists():
        shutil.rmtree(app_dir)
        return True
    return False
