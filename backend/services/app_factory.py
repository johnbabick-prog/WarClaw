"""Reliable scaffold-based app generation for WarClaw."""
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from .agent_engine import agent_engine
from .chat_sessions import list_sessions
from .hardware import detect_hardware
from .llm import llm_service
from .mission_log import recent
from .traffic_monitor import traffic_monitor
from ..config import GENERATED_APPS_DIR

log = logging.getLogger("warclaw.factory")

ALLOWED_WIDGETS = {
    "status_summary",
    "lan_overview",
    "agents_overview",
    "traffic_overview",
    "mission_log",
    "generated_apps",
    "hardware_overview",
    "chat_activity",
}

TEXT_FILE_EXTENSIONS = {
    ".txt", ".md", ".log", ".json", ".csv", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".ini", ".cfg", ".conf", ".nmea",
}

SPEC_PROMPT = """You are configuring a WarClaw operational dashboard.
Return JSON only with this schema:
{{
  "summary": "string",
  "widgets": [
    {{"type": "status_summary|lan_overview|agents_overview|traffic_overview|mission_log|generated_apps|hardware_overview|chat_activity", "title": "string", "description": "string"}}
  ]
}}

Choose 3 to 6 widgets that best match the request.
App name: {app_name}
Description: {description}
Context: {context}
"""

ITERATION_PROMPT = """You are refining an existing WarClaw generated app.
Return JSON only with this schema:
{{
  "name": "string",
  "description": "string",
  "context": "string",
  "reply": "string"
}}

Rules:
- Preserve the app's purpose unless the operator explicitly asks to change it.
- Apply the operator's requested changes to layout, functionality, emphasis, workflow, or color direction.
- Keep the description concrete and implementation-oriented.
- Keep the context concise and cumulative.
- The reply should briefly explain what changed.

Current app name: {app_name}
Current description: {description}
Current context: {context}
Current summary: {summary}
Operator instruction: {instruction}
"""

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:40]


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _clean_copy(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    return cleaned.strip(" .")


def _copy_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_copy(text).lower())


def _request_tail(text: str) -> str:
    cleaned = _clean_copy(text)
    cleaned = re.sub(r"^(create|build|generate|make|consider creating)( me)?( a| an)?\s+", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _normalize_app_name(app_name: str, description: str, app_kind: str) -> str:
    requested = _clean_copy(app_name)
    desc = _request_tail(description)
    match = re.search(r"(ship systems overview|navigation dashboard|report generator|lan investigator|engineering plant monitor)", desc, flags=re.IGNORECASE)
    if match:
        return match.group(1).title()
    if not requested or any(term in requested.lower() for term in ("detected", "recommendation", "generated app", "workspace")):
        defaults = {
            "report_generator": "Report Generator",
            "navigation_dashboard": "Navigation Dashboard",
            "lan_investigator": "LAN Investigator",
            "operational_dashboard": "Ship Systems Overview",
        }
        return defaults.get(app_kind, "Generated App")
    return requested


def _compose_app_description(app_kind: str, description: str, context: str) -> str:
    desc = _request_tail(description).lower()
    if "overview" in desc or "host density" in desc:
        return "Consolidate discovered hosts, services, mission events, and active tooling into one operational picture."
    if app_kind == "task_workspace":
        return "Capture unfinished actions, build the next watch checklist, and track task completion in a persistent daily workspace."
    if app_kind == "report_generator":
        return "Upload source files and turn them into structured operational summaries, briefs, and watch-ready reports."
    if app_kind == "navigation_dashboard":
        return "Track bridge navigation data with live position, heading, speed, depth, and source visibility."
    if app_kind == "lan_investigator":
        return "Scan the local network, inspect hosts and services, and surface follow-on operational actions."
    return "Monitor WarClaw system posture, mission activity, and generated tooling from a single workspace."


def _compose_app_summary(app_kind: str, description: str, context: str) -> str:
    context_clean = _clean_copy(context)
    if app_kind == "task_workspace":
        base = "Supports operator task entry, carry-forward planning, completion tracking, and handoff-ready daily lists."
    elif app_kind == "report_generator":
        base = "Supports upload-driven reporting with reusable summaries, recommended actions, and markdown export."
    elif app_kind == "navigation_dashboard":
        base = "Grounds navigation awareness in decoded NMEA traffic and candidate bridge-system sources."
    elif app_kind == "lan_investigator":
        base = "Pairs host discovery with recommendations so operators can move from scan results to action quickly."
    else:
        base = "Provides a concise operational dashboard for current WarClaw posture, mission activity, and operator workflows."
    if context_clean:
        return f"{base} Integration context: {context_clean[:180]}."
    return base


def _prepare_blueprint(app_name: str, description: str, context: str) -> dict:
    app_kind = _detect_app_kind(app_name, description, context)
    normalized_name = _normalize_app_name(app_name, description, app_kind)
    normalized_description = _compose_app_description(app_kind, description, context)
    summary = _compose_app_summary(app_kind, description, context)
    return {
        "name": normalized_name,
        "description": normalized_description,
        "context": context,
        "app_kind": app_kind,
        "summary": summary,
    }


def _app_manifest_path(slug: str) -> Path:
    return GENERATED_APPS_DIR / slug / "manifest.json"


def _load_manifest(slug: str) -> dict:
    path = _app_manifest_path(slug)
    if not path.exists():
        raise FileNotFoundError(slug)
    return json.loads(path.read_text(encoding="utf-8"))


def _save_manifest(slug: str, manifest: dict) -> None:
    _app_manifest_path(slug).write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _heuristic_iterative_update(existing: dict, instruction: str) -> dict:
    name = existing.get("name", "Generated App")
    description = existing.get("description", "")
    context = existing.get("context", "")
    lowered = instruction.lower()

    if any(term in lowered for term in ("layout", "rearrange", "compact", "denser", "sidebar", "grid")):
        description = f"{description.rstrip('.')} Present key data in a cleaner, more intentional layout with stronger hierarchy."
    if any(term in lowered for term in ("color", "theme", "palette", "scheme")):
        context = f"{context.rstrip('. ')} Visual direction requested: {instruction.strip()}".strip()
    if any(term in lowered for term in ("function", "feature", "add", "workflow", "filter", "search")):
        description = f"{description.rstrip('.')} Include the requested workflow changes: {instruction.strip()}."
    elif instruction.strip():
        context = f"{context.rstrip('. ')} Refinement request: {instruction.strip()}".strip()

    return {
        "name": name,
        "description": description,
        "context": context,
        "reply": "I updated the app brief and regenerated the app around your requested changes.",
    }


async def _refine_blueprint(existing: dict, instruction: str) -> dict:
    instruction = _clean_copy(instruction)
    if not instruction:
        raise ValueError("Iteration instruction is required")

    if not llm_service.ready:
        return _heuristic_iterative_update(existing, instruction)

    prompt = ITERATION_PROMPT.format(
        app_name=existing.get("name", "Generated App"),
        description=existing.get("description", ""),
        context=existing.get("context", "") or "No additional context.",
        summary=existing.get("operator_summary", existing.get("description", "")),
        instruction=instruction,
    )
    raw = "".join([
        token async for token in llm_service.astream_chat([], prompt, max_tokens=700, temperature=0.2)
    ])
    try:
        parsed = json.loads(_strip_fences(raw))
    except Exception:
        return _heuristic_iterative_update(existing, instruction)

    name = _clean_copy(parsed.get("name") or existing.get("name", "Generated App"))
    description = _clean_copy(parsed.get("description") or existing.get("description", ""))
    context = _clean_copy(parsed.get("context") or existing.get("context", ""))
    reply = _clean_copy(parsed.get("reply") or "I updated the app brief and regenerated the app.")

    return {
        "name": name or existing.get("name", "Generated App"),
        "description": description or existing.get("description", ""),
        "context": context,
        "reply": reply,
    }


def _workspace_close_script() -> str:
    return """
    function closeWorkspace() {
      if (window.opener && !window.opener.closed) {
        window.close();
        return;
      }
      if (window.history.length > 1) {
        window.history.back();
        return;
      }
      window.location.href = '/';
    }
    """


def _workspace_actions(slug: str) -> str:
    return f"""
      <div class="hero-actions">
        <a class="hero-link" href="{_workspace_edit_link(slug)}">Open In App Factory</a>
        <button class="hero-link" type="button" onclick="closeWorkspace()">✕ Close</button>
      </div>
    """


def _heuristic_widgets(description: str, context: str) -> list[dict]:
    blob = f"{description} {context}".lower()
    widgets = [
        {"type": "status_summary", "title": "System Status", "description": "Current WarClaw core and model posture."},
    ]
    if any(term in blob for term in ("lan", "network", "nmea", "modbus", "sensor", "ship system", "navigation")):
        widgets.append({"type": "lan_overview", "title": "LAN Overview", "description": "Last discovered hosts, services, and recommendations."})
    if any(term in blob for term in ("agent", "monitor", "watch", "engineering")):
        widgets.append({"type": "agents_overview", "title": "Agent Posture", "description": "Deployed monitoring agents and status."})
    if any(term in blob for term in ("traffic", "stream", "packet", "protocol")):
        widgets.append({"type": "traffic_overview", "title": "Traffic Snapshot", "description": "Recent traffic statistics and conversations."})
    if any(term in blob for term in ("report", "log", "summary", "opord", "brief")):
        widgets.append({"type": "mission_log", "title": "Mission Log", "description": "Recent mission events and alerts."})
    if any(term in blob for term in ("app", "dashboard", "workspace")):
        widgets.append({"type": "generated_apps", "title": "Generated Apps", "description": "Existing generated apps relevant to this workflow."})
    if any(term in blob for term in ("hardware", "gpu", "cpu", "model")):
        widgets.append({"type": "hardware_overview", "title": "Hardware Profile", "description": "Server and model posture."})
    widgets.append({"type": "chat_activity", "title": "Operator Questions", "description": "Recent AI chat session activity."})

    deduped = []
    seen = set()
    for widget in widgets:
        if widget["type"] in seen:
            continue
        seen.add(widget["type"])
        deduped.append(widget)
    return deduped[:6]


def _visual_profile(description: str, context: str) -> dict:
    blob = f"{description} {context}".lower()
    theme = "naval"
    density = "comfortable"

    if any(term in blob for term in ("red", "crimson", "scarlet")):
        theme = "red"
    elif any(term in blob for term in ("green", "emerald")):
        theme = "green"
    elif any(term in blob for term in ("amber", "orange", "gold")):
        theme = "amber"
    elif any(term in blob for term in ("steel blue", "blue", "cyan", "ice")):
        theme = "blue"

    if any(term in blob for term in ("compact", "denser", "dense", "tight", "more data")):
        density = "compact"
    elif any(term in blob for term in ("spacious", "airy", "larger cards")):
        density = "spacious"

    return {"theme": theme, "density": density}


def _style_tokens(spec: dict) -> dict:
    theme = (spec.get("theme") or "naval").lower()
    density = (spec.get("density") or "comfortable").lower()

    palettes = {
        "naval": {
            "bg": "#071019",
            "bg_alt": "#050c13",
            "panel": "#0d1823",
            "panel_2": "#101d2c",
            "border": "rgba(101, 216, 255, 0.18)",
            "text": "#ecf4f7",
            "muted": "#8ca7b6",
            "accent": "#ff6f3b",
            "accent_2": "#65d8ff",
        },
        "red": {
            "bg": "#1a0909",
            "bg_alt": "#120606",
            "panel": "#231010",
            "panel_2": "#2b1414",
            "border": "rgba(255, 111, 91, 0.24)",
            "text": "#fff0ec",
            "muted": "#d7aaa0",
            "accent": "#ff5847",
            "accent_2": "#ff9a7a",
        },
        "green": {
            "bg": "#07140f",
            "bg_alt": "#04100b",
            "panel": "#0d2119",
            "panel_2": "#123024",
            "border": "rgba(87, 209, 140, 0.22)",
            "text": "#ecf7ef",
            "muted": "#9ec0aa",
            "accent": "#3ecf8e",
            "accent_2": "#8ff0c2",
        },
        "amber": {
            "bg": "#161006",
            "bg_alt": "#0f0a04",
            "panel": "#241a0d",
            "panel_2": "#302211",
            "border": "rgba(255, 178, 36, 0.24)",
            "text": "#fbf2df",
            "muted": "#cfba8d",
            "accent": "#ff9f1a",
            "accent_2": "#ffd166",
        },
        "blue": {
            "bg": "#06111a",
            "bg_alt": "#040b12",
            "panel": "#0d1b29",
            "panel_2": "#112334",
            "border": "rgba(97, 170, 255, 0.22)",
            "text": "#eef6ff",
            "muted": "#9eb6d0",
            "accent": "#4d96ff",
            "accent_2": "#86c5ff",
        },
    }
    spacing = {
        "compact": {"shell_width": "1180px", "gap_size": "12px", "hero_pad": "20px", "panel_pad": "14px", "radius_size": "16px"},
        "spacious": {"shell_width": "1440px", "gap_size": "22px", "hero_pad": "28px", "panel_pad": "20px", "radius_size": "22px"},
        "comfortable": {"shell_width": "1280px", "gap_size": "16px", "hero_pad": "24px", "panel_pad": "18px", "radius_size": "18px"},
    }

    return {**palettes.get(theme, palettes["naval"]), **spacing.get(density, spacing["comfortable"])}


def _theme_palette_from_instruction(instruction: str) -> Optional[dict]:
    lowered = instruction.lower()
    if "blue" in lowered:
        return {
            "--bg": "#071627",
            "--bg-alt": "#040c16",
            "--panel": "#0f2238",
            "--panel-2": "#14304d",
            "--border": "rgba(77, 150, 255, 0.32)",
            "--text": "#eef6ff",
            "--muted": "#a9c0dd",
            "--accent": "#4d96ff",
            "--accent-2": "#8ac5ff",
        }
    if "red" in lowered:
        return {
            "--bg": "#1a0909",
            "--bg-alt": "#120606",
            "--panel": "#231010",
            "--panel-2": "#2b1414",
            "--border": "rgba(255, 111, 91, 0.24)",
            "--text": "#fff0ec",
            "--muted": "#d7aaa0",
            "--accent": "#ff5847",
            "--accent-2": "#ff9a7a",
        }
    if "green" in lowered:
        return {
            "--bg": "#07140f",
            "--bg-alt": "#04100b",
            "--panel": "#0d2119",
            "--panel-2": "#123024",
            "--border": "rgba(87, 209, 140, 0.28)",
            "--text": "#ecf7ef",
            "--muted": "#9ec0aa",
            "--accent": "#3ecf8e",
            "--accent-2": "#8ff0c2",
        }
    if "yellow" in lowered or "amber" in lowered or "orange" in lowered:
        return {
            "--bg": "#161006",
            "--bg-alt": "#0f0a04",
            "--panel": "#241a0d",
            "--panel-2": "#302211",
            "--border": "rgba(255, 178, 36, 0.28)",
            "--text": "#fbf2df",
            "--muted": "#cfba8d",
            "--accent": "#ff9f1a",
            "--accent-2": "#ffd166",
        }
    return None


def _replace_css_var(html: str, var_name: str, value: str) -> str:
    pattern = rf"({re.escape(var_name)}\s*:\s*)([^;]+)(;)"
    return re.sub(pattern, rf"\g<1>{value}\3", html, count=1)


def _apply_instruction_to_html(html: str, instruction: str) -> str:
    updated = html
    palette = _theme_palette_from_instruction(instruction)
    if palette:
        for var_name, value in palette.items():
            updated = _replace_css_var(updated, var_name, value)

    lowered = instruction.lower()
    if any(term in lowered for term in ("compact", "denser", "dense", "tight")):
        updated = re.sub(r"gap:\s*18px", "gap: 12px", updated)
        updated = re.sub(r"gap:\s*16px", "gap: 12px", updated)
        updated = re.sub(r"padding:\s*24px", "padding: 18px", updated)
        updated = re.sub(r"padding:\s*18px", "padding: 14px", updated)
    if any(term in lowered for term in ("spacious", "airy", "bigger")):
        updated = re.sub(r"gap:\s*12px", "gap: 20px", updated)
        updated = re.sub(r"gap:\s*16px", "gap: 20px", updated)
        updated = re.sub(r"padding:\s*14px", "padding: 20px", updated)
        updated = re.sub(r"padding:\s*18px", "padding: 22px", updated)

    if "source files section" in lowered and "orange" in lowered:
        updated = re.sub(r"(\.dropzone\s*\{[^}]*border:\s*1px dashed )([^;]+)(;)", r"\1rgba(255, 159, 26, 0.55)\3", updated, count=1, flags=re.S)
        updated = re.sub(r"(\.dropzone\s*\{[^}]*background:\s*)([^;]+)(;)", r"\1rgba(255, 159, 26, 0.08)\3", updated, count=1, flags=re.S)

    return updated


async def _rewrite_app_frontend(slug: str, instruction: str, html: str) -> str:
    return _apply_instruction_to_html(html, instruction)


async def _build_spec(app_name: str, description: str, context: str, app_kind: Optional[str] = None, summary_seed: str = "") -> dict:
    widgets = _heuristic_widgets(description, context)
    summary = summary_seed or description
    app_kind = app_kind or _detect_app_kind(app_name, description, context)
    visual = _visual_profile(description, context)

    if llm_service.ready:
        prompt = SPEC_PROMPT.format(app_name=app_name, description=description, context=context or "No additional context.")
        raw = "".join([
            token async for token in llm_service.astream_chat([], prompt, max_tokens=900, temperature=0.2)
        ])
        try:
            parsed = json.loads(_strip_fences(raw))
            selected = []
            for widget in parsed.get("widgets", []):
                widget_type = widget.get("type", "")
                if widget_type not in ALLOWED_WIDGETS:
                    continue
                selected.append({
                    "type": widget_type,
                    "title": widget.get("title") or widget_type.replace("_", " ").title(),
                    "description": widget.get("description") or "",
                })
            if selected:
                widgets = selected[:6]
            parsed_summary = _clean_copy(parsed.get("summary") or "")
            if parsed_summary and len(parsed_summary.split()) >= 6 and _copy_key(parsed_summary) != _copy_key(description):
                summary = parsed_summary
        except Exception:
            pass

    return {
        "app_kind": app_kind,
        "summary": summary,
        "widgets": widgets,
        "theme": visual["theme"],
        "density": visual["density"],
    }


def _detect_app_kind(app_name: str, description: str, context: str) -> str:
    blob = f"{app_name} {description} {context}".lower()
    if any(term in blob for term in ("todo", "to do", "task list", "checklist", "carry forward", "daily list")):
        return "task_workspace"
    if any(term in blob for term in ("report", "summary", "opord", "brief", "upload files", "upload documents", "document")):
        return "report_generator"
    if any(term in blob for term in ("navigation", "nmea", "heading", "depth", "speed", "gps", "bridge")):
        return "navigation_dashboard"
    if any(term in blob for term in ("lan", "network", "host", "port scan", "traffic", "discovery", "systems overview", "investigator")):
        return "lan_investigator"
    return "operational_dashboard"


def _app_dir(slug: str) -> Path:
    return GENERATED_APPS_DIR / slug


def _uploads_dir(slug: str) -> Path:
    return _app_dir(slug) / "uploads"


def _reports_dir(slug: str) -> Path:
    return _app_dir(slug) / "reports"


def _tasks_path(slug: str) -> Path:
    return _app_dir(slug) / "tasks.json"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip("-")
    return cleaned or f"upload-{int(time.time())}"


def _read_text_preview(path: Path, limit: int = 6000) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext not in TEXT_FILE_EXTENSIONS:
        return "", f"Unsupported file type for inline summarization: {ext or 'unknown'}"
    try:
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
    except Exception as exc:
        return "", f"Failed to read file: {exc}"
    if not text:
        return "", "File is empty."
    return text[:limit], ""


def _extract_structured_brief(previews: list[dict]) -> dict:
    findings = {
        "mission": [],
        "risks": [],
        "decisions": [],
        "next_actions": [],
        "highlights": [],
    }
    aliases = {
        "mission": "mission",
        "objective": "mission",
        "risks": "risks",
        "risk": "risks",
        "decisions": "decisions",
        "decision": "decisions",
        "next actions": "next_actions",
        "actions": "next_actions",
        "action items": "next_actions",
    }

    for item in previews:
        for raw_line in item["preview"].splitlines():
            line = raw_line.strip(" -*\t")
            if not line:
                continue
            lowered = line.lower()
            matched = False
            for label, key in aliases.items():
                prefix = f"{label}:"
                if lowered.startswith(prefix):
                    value = line[len(prefix):].strip()
                    if value:
                        findings[key].append(value)
                    matched = True
                    break
            if not matched and len(findings["highlights"]) < 6:
                findings["highlights"].append(line[:240])
    return findings


def _sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def list_app_files(slug: str) -> list[dict]:
    uploads_dir = _uploads_dir(slug)
    if not uploads_dir.exists():
        return []
    items = []
    for path in sorted(uploads_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        items.append({
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "updated_at": path.stat().st_mtime,
            "suffix": path.suffix.lower(),
        })
    return items


def list_app_reports(slug: str) -> list[dict]:
    reports_dir = _reports_dir(slug)
    if not reports_dir.exists():
        return []
    items = []
    for path in sorted(reports_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name == "latest.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({
            "id": path.stem,
            "title": payload.get("title") or path.stem,
            "summary": payload.get("summary") or "",
            "generated_at": payload.get("generated_at") or path.stat().st_mtime,
            "report_type": payload.get("report_type") or "uploaded_report",
        })
    return items


def delete_app_file(slug: str, name: str) -> bool:
    path = _uploads_dir(slug) / Path(name).name
    if not path.exists() or not path.is_file():
        return False
    path.unlink()
    return True


def delete_app_report(slug: str, report_id: str) -> bool:
    reports_dir = _reports_dir(slug)
    removed = False
    for suffix in (".json", ".md"):
        path = reports_dir / f"{Path(report_id).name}{suffix}"
        if path.exists() and path.is_file():
            path.unlink()
            removed = True
    latest_path = reports_dir / "latest.json"
    latest = get_latest_app_report(slug)
    if latest and latest.get("id") == report_id and latest_path.exists():
        latest_path.unlink()
    return removed


def list_app_tasks(slug: str) -> list[dict]:
    path = _tasks_path(slug)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    tasks = payload.get("tasks", [])
    if not isinstance(tasks, list):
        return []
    tasks.sort(key=lambda item: (item.get("completed", False), -(item.get("updated_at", 0) or 0)))
    return tasks


def save_app_tasks(slug: str, tasks: list[dict]) -> None:
    _app_dir(slug).mkdir(parents=True, exist_ok=True)
    _tasks_path(slug).write_text(json.dumps({"tasks": tasks}, indent=2), encoding="utf-8")


def add_app_task(slug: str, title: str, notes: str = "", due_label: str = "", source: str = "manual") -> dict:
    tasks = list_app_tasks(slug)
    task = {
        "id": f"task-{int(time.time() * 1000)}",
        "title": _clean_copy(title),
        "notes": _clean_copy(notes),
        "due_label": _clean_copy(due_label),
        "source": source,
        "completed": False,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    tasks.append(task)
    save_app_tasks(slug, tasks)
    return task


def update_app_task(slug: str, task_id: str, *, title: Optional[str] = None, notes: Optional[str] = None,
                    due_label: Optional[str] = None, completed: Optional[bool] = None) -> dict:
    tasks = list_app_tasks(slug)
    for task in tasks:
        if task.get("id") != task_id:
            continue
        if title is not None:
            task["title"] = _clean_copy(title)
        if notes is not None:
            task["notes"] = _clean_copy(notes)
        if due_label is not None:
            task["due_label"] = _clean_copy(due_label)
        if completed is not None:
            task["completed"] = bool(completed)
        task["updated_at"] = time.time()
        save_app_tasks(slug, tasks)
        return task
    raise FileNotFoundError(task_id)


def delete_app_task(slug: str, task_id: str) -> bool:
    tasks = list_app_tasks(slug)
    kept = [task for task in tasks if task.get("id") != task_id]
    if len(kept) == len(tasks):
        return False
    save_app_tasks(slug, kept)
    return True


def get_latest_app_report(slug: str) -> Optional[dict]:
    latest_path = _reports_dir(slug) / "latest.json"
    if not latest_path.exists():
        return None
    try:
        return json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _to_markdown_report(report: dict) -> str:
    lines = [
        f"# {report.get('title', 'Generated Report')}",
        "",
        report.get("summary", ""),
        "",
    ]
    for section in report.get("sections", []):
        lines.append(f"## {section.get('heading', 'Section')}")
        lines.append("")
        lines.append(section.get("body", ""))
        lines.append("")
    actions = report.get("recommended_actions", [])
    if actions:
        lines.append("## Recommended Actions")
        lines.append("")
        for action in actions:
            lines.append(f"- {action}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def persist_app_report(slug: str, report: dict) -> dict:
    reports_dir = _reports_dir(slug)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_id = time.strftime("report-%Y%m%d-%H%M%S")
    payload = dict(report)
    payload["id"] = report_id
    payload["generated_at"] = payload.get("generated_at") or time.time()
    json_path = reports_dir / f"{report_id}.json"
    md_path = reports_dir / f"{report_id}.md"
    latest_path = reports_dir / "latest.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown_report(payload), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


async def summarize_app_files(slug: str, prompt: str = "", report_type: str = "uploaded_report") -> dict:
    spec = get_app_spec(slug)
    if spec is None:
        raise FileNotFoundError(f"App not found: {slug}")

    files = list_app_files(slug)
    if not files:
        raise ValueError("No uploaded files found for this app")

    previews = []
    unsupported = []
    for file_info in files[:8]:
        path = _uploads_dir(slug) / file_info["name"]
        preview, warning = _read_text_preview(path)
        if preview:
            previews.append({
                "name": file_info["name"],
                "preview": preview,
                "size_bytes": file_info["size_bytes"],
            })
        elif warning:
            unsupported.append(f"{file_info['name']}: {warning}")

    if not previews and unsupported:
        raise ValueError("Uploaded files are not readable text formats yet")

    summary = None
    sections = []
    recommended_actions = []
    app_name = (_app_dir(slug) / "manifest.json")
    try:
        manifest = json.loads(app_name.read_text(encoding="utf-8"))
        app_name = manifest.get("name", slug)
    except Exception:
        app_name = slug

    if llm_service.ready and previews:
        llm_prompt = f"""Create a concise operational report from uploaded files for a generated WarClaw app.

App: {app_name}
Report type: {report_type}
Operator focus: {prompt or "Summarize the uploaded material for ship operators."}

Return JSON only:
{{
  "title": "string",
  "summary": "string",
  "sections": [
    {{"heading": "string", "body": "string"}}
  ],
  "recommended_actions": ["string"]
}}

Uploaded file excerpts:
{json.dumps(previews, indent=2)}
"""
        raw = "".join([
            token async for token in llm_service.astream_chat([], llm_prompt, max_tokens=1400, temperature=0.2)
        ]).strip()
        try:
            parsed = json.loads(_strip_fences(raw))
            summary = parsed.get("summary") or ""
            sections = parsed.get("sections") or []
            recommended_actions = parsed.get("recommended_actions") or []
            title = parsed.get("title") or f"{app_name} Summary"
        except Exception:
            title = f"{app_name} Summary"
    else:
        title = f"{app_name} Summary"

    if not summary:
        extracted = _extract_structured_brief(previews)
        bullets = []
        for item in previews[:4]:
            first_line = item["preview"].splitlines()[0][:220]
            bullets.append(f"{item['name']}: {first_line}")

        mission_text = extracted["mission"][0] if extracted["mission"] else None
        risk_text = "; ".join(extracted["risks"][:3]) if extracted["risks"] else None
        next_actions_text = extracted["next_actions"][:4]

        summary_parts = [f"Reviewed {len(files)} uploaded file(s)."]
        if mission_text:
            summary_parts.append(f"Mission: {_sentence(mission_text)}")
        if risk_text:
            summary_parts.append(f"Primary risks: {_sentence(risk_text)}")
        if next_actions_text:
            summary_parts.append(f"Next actions: {_sentence('; '.join(next_actions_text))}")
        if len(summary_parts) == 1:
            summary_parts.append("Key excerpts: " + " | ".join(bullets[:3]))
        summary = " ".join(summary_parts)

        sections = sections or [
            {
                "heading": "Operational Overview",
                "body": mission_text or "\n".join(f"- {item}" for item in extracted["highlights"][:4]) or "No operational overview extracted.",
            },
            {
                "heading": "Risks And Decisions",
                "body": "\n".join(
                    ([f"- Risk: {item}" for item in extracted["risks"][:4]] +
                     [f"- Decision: {item}" for item in extracted["decisions"][:4]])
                ) or "No explicit risks or decisions were extracted from the uploaded text.",
            },
            {
                "heading": "Uploaded Sources",
                "body": "\n".join(f"- {item['name']} ({item['size_bytes']} bytes)" for item in files[:8]),
            },
        ]
        recommended_actions = recommended_actions or (
            extracted["next_actions"][:5] if extracted["next_actions"] else [
                "Review the generated summary against the source files.",
                "Upload additional plain-text files for a fuller summary.",
                "Convert binary documents to text or markdown for better extraction quality.",
            ]
        )

    report = {
        "title": title,
        "summary": summary,
        "sections": sections,
        "recommended_actions": recommended_actions,
        "files": files,
        "unsupported": unsupported,
        "report_type": report_type,
        "generated_at": time.time(),
    }
    return persist_app_report(slug, report)


def _render_widget(widget: dict) -> str:
    return f"""
      <section class="widget-card" data-widget="{widget['type']}">
        <div class="widget-head">
          <div>
            <div class="widget-title">{widget['title']}</div>
            <div class="widget-copy">{widget.get('description', '')}</div>
          </div>
        </div>
        <div class="widget-body" id="widget-{widget['type']}">Loading...</div>
      </section>
    """


def _hero_secondary_copy(description: str, summary: str) -> str:
    if not summary or _copy_key(summary) == _copy_key(description):
        return ""
    return f'<div class="hero-copy" style="margin-top:8px;">{summary}</div>'


def _workspace_edit_link(slug: str) -> str:
    return f'/?view=apps&edit={slug}'


def _render_app_html(slug: str, app_name: str, description: str, spec: dict) -> str:
    if spec.get("app_kind") == "task_workspace":
        return _render_task_app_html(slug, app_name, description, spec)
    if spec.get("app_kind") == "report_generator":
        return _render_report_app_html(slug, app_name, description, spec)
    if spec.get("app_kind") == "navigation_dashboard":
        return _render_navigation_app_html(slug, app_name, description, spec)
    if spec.get("app_kind") == "lan_investigator":
        return _render_lan_app_html(slug, app_name, description, spec)
    style = _style_tokens(spec)
    cards = "\n".join(_render_widget(widget) for widget in spec["widgets"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{app_name} - WarClaw</title>
  <style>
    :root {{
      --bg: {style["bg"]};
      --bg-alt: {style["bg_alt"]};
      --panel: {style["panel"]};
      --panel-2: {style["panel_2"]};
      --border: {style["border"]};
      --text: {style["text"]};
      --muted: {style["muted"]};
      --accent: {style["accent"]};
      --accent-2: {style["accent_2"]};
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(101, 216, 255, 0.18), transparent 30%),
        radial-gradient(circle at top left, rgba(255, 111, 59, 0.14), transparent 28%),
        linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{ max-width: {style["shell_width"]}; margin: 0 auto; padding: 28px; }}
    .hero {{
      padding: {style["hero_pad"]};
      border: 1px solid var(--border);
      background: rgba(13, 24, 35, 0.9);
      border-radius: {style["radius_size"]};
      margin-bottom: 20px;
    }}
    .eyebrow {{ color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.18em; font-size: 11px; }}
    h1 {{ margin: 10px 0 8px; font-size: 32px; }}
    .hero-copy {{ color: var(--muted); line-height: 1.7; max-width: 900px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: {style["gap_size"]}; }}
    .widget-card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: {style["radius_size"]};
      padding: {style["panel_pad"]};
      min-height: 220px;
    }}
    .widget-title {{ font-size: 17px; font-weight: 700; }}
    .widget-copy {{ color: var(--muted); font-size: 12px; margin-top: 6px; }}
    .widget-body {{ margin-top: 16px; color: var(--text); font-size: 14px; line-height: 1.7; }}
    .hero-actions {{ margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .hero-link {{
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 12px; padding: 10px 14px; text-decoration: none;
      background: rgba(101,216,255,0.12); color: var(--accent-2); border: 1px solid var(--border);
      font-size: 13px; font-weight: 700;
    }}
    .metric {{ display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }}
    .metric:last-child {{ border-bottom: 0; }}
    .label {{ color: var(--muted); }}
    .value {{ color: var(--text); text-align: right; }}
    .pill {{ display: inline-block; margin: 0 6px 6px 0; padding: 6px 10px; border-radius: 999px; background: rgba(101,216,255,0.12); color: var(--accent-2); font-size: 11px; }}
    .event {{ padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }}
    .event:last-child {{ border-bottom: 0; }}
    a {{ color: var(--accent-2); }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Generated Operational Workspace</div>
      <h1>{app_name}</h1>
      <div class="hero-copy">{description}</div>
      {_hero_secondary_copy(description, spec['summary'])}
{_workspace_actions(slug)}
    </section>
    <section class="grid">
      {cards}
    </section>
  </div>
  <script>
    const slug = {json.dumps(slug)};

    function esc(text) {{
      return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }}

    {_workspace_close_script()}

    function metric(label, value) {{
      return `<div class="metric"><div class="label">${{esc(label)}}</div><div class="value">${{esc(value)}}</div></div>`;
    }}

    function renderWidget(type, data) {{
      const el = document.getElementById(`widget-${{type}}`);
      if (!el) return;

      if (type === 'status_summary') {{
        el.innerHTML = [
          metric('Model Ready', data.status_summary.model_ready ? 'Yes' : 'No'),
          metric('Current Model', data.status_summary.current_model || 'None'),
          metric('Generated Apps', data.status_summary.generated_app_count),
          metric('Agent Count', data.status_summary.agent_count),
        ].join('');
        return;
      }}

      if (type === 'lan_overview') {{
        if (!data.lan_overview) {{
          el.innerHTML = '<div class="label">No LAN scan has been persisted yet.</div>';
          return;
        }}
        const hosts = (data.lan_overview.hosts || []).slice(0, 8).map(host => `<div class="event"><strong>${{esc(host.ip)}}</strong><br><span class="label">${{esc((host.services || []).map(s => `${{s.port}}/${{s.protocol}}`).join(', ') || 'No identified services')}}</span></div>`).join('');
        el.innerHTML = metric('Network', data.lan_overview.network)
          + metric('Hosts Up', data.lan_overview.hosts_up)
          + `<div style="margin-top:10px">${{hosts || '<div class="label">No host details available.</div>'}}</div>`;
        return;
      }}

      if (type === 'agents_overview') {{
        const agents = (data.agents || []).slice(0, 8);
        el.innerHTML = agents.length
          ? agents.map(agent => `<div class="event"><strong>${{esc(agent.name || agent.agent_type)}}</strong><br><span class="label">${{esc(agent.status)}} · ${{esc(agent.target_host || 'no target')}}</span></div>`).join('')
          : '<div class="label">No deployed agents.</div>';
        return;
      }}

      if (type === 'traffic_overview') {{
        const conversations = (data.traffic.conversations || []).slice(0, 6);
        el.innerHTML = [
          metric('Packets', data.traffic.stats.packets_captured || 0),
          metric('Conversations', data.traffic.stats.total_conversations || 0),
          metric('Capture Running', data.traffic.stats.running ? 'Yes' : 'No'),
          `<div style="margin-top:10px">${{conversations.map(conv => `<span class="pill">${{esc(conv.protocol || 'UNKNOWN')}} ${{esc(conv.src || '?')}} -> ${{esc(conv.dst || '?')}}</span>`).join('') || '<div class="label">No traffic conversations captured.</div>'}}</div>`
        ].join('');
        return;
      }}

      if (type === 'mission_log') {{
        const events = (data.events || []).slice(0, 8);
        el.innerHTML = events.length
          ? events.map(evt => `<div class="event"><strong>${{esc(evt.category)}}</strong> · <span class="label">${{esc(evt.utc || '')}}</span><br>${{esc(evt.message)}}</div>`).join('')
          : '<div class="label">No mission log events yet.</div>';
        return;
      }}

      if (type === 'generated_apps') {{
        const apps = (data.generated_apps || []).slice(0, 8);
        el.innerHTML = apps.length
          ? apps.map(app => `<div class="event"><strong>${{esc(app.name)}}</strong><br><span class="label">${{esc(app.description || '')}}</span><br><a href="/api/apps/${{esc(app.slug)}}/ui" target="_blank" rel="noopener">Open app</a></div>`).join('')
          : '<div class="label">No generated apps available.</div>';
        return;
      }}

      if (type === 'hardware_overview') {{
        const hw = data.hardware;
        el.innerHTML = [
          metric('CPU', hw.cpu_model || `${{hw.cpu_cores}} cores`),
          metric('RAM', `${{hw.ram_gb}} GB`),
          metric('GPU', hw.gpu_name || 'None'),
          metric('Recommended Tier', hw.recommended_tier || 'Unknown'),
          metric('Suggested Model', hw.recommended_model || 'Unknown'),
        ].join('');
        return;
      }}

      if (type === 'chat_activity') {{
        const sessions = (data.chat_sessions || []).slice(0, 6);
        el.innerHTML = sessions.length
          ? sessions.map(session => `<div class="event"><strong>${{esc(session.title)}}</strong><br><span class="label">${{esc(session.message_count)}} messages</span></div>`).join('')
          : '<div class="label">No saved chat sessions yet.</div>';
        return;
      }}

      el.textContent = 'No renderer for widget.';
    }}

    async function init() {{
      const response = await fetch(`/api/apps/${{slug}}/data`);
      const data = await response.json();
      (data.app.widgets || []).forEach(widget => renderWidget(widget.type, data));
    }}

    init().catch(err => {{
      document.querySelectorAll('.widget-body').forEach(el => {{
        el.textContent = `Failed to load app data: ${{err.message}}`;
      }});
    }});
  </script>
</body>
</html>"""


def _render_task_app_html(slug: str, app_name: str, description: str, spec: dict) -> str:
    style = _style_tokens(spec)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{app_name} - WarClaw</title>
  <style>
    :root {{
      --bg: {style["bg"]};
      --bg-alt: {style["bg_alt"]};
      --panel: {style["panel"]};
      --panel-2: {style["panel_2"]};
      --border: {style["border"]};
      --text: {style["text"]};
      --muted: {style["muted"]};
      --accent: {style["accent"]};
      --accent-2: {style["accent_2"]};
      --good: #57d18c;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family:"Avenir Next","Segoe UI",sans-serif; color:var(--text); background:linear-gradient(180deg,var(--bg-alt) 0%,var(--bg) 100%); min-height:100vh; }}
    .shell {{ max-width: {style["shell_width"]}; margin: 0 auto; padding: 28px; }}
    .hero,.panel {{ background:var(--panel); border:1px solid var(--border); border-radius:{style["radius_size"]}; }}
    .hero {{ padding:{style["hero_pad"]}; margin-bottom:18px; }}
    .eyebrow {{ color: var(--accent-2); text-transform: uppercase; letter-spacing: .18em; font-size:11px; }}
    h1 {{ margin:10px 0 8px; font-size:40px; }}
    .sub {{ color: var(--muted); max-width:920px; line-height:1.7; }}
    .hero-actions {{ margin-top:16px; display:flex; gap:10px; flex-wrap:wrap; }}
    .hero-link {{ display:inline-flex; align-items:center; justify-content:center; border-radius:12px; padding:10px 14px; text-decoration:none; background:#162334; color:var(--text); border:1px solid rgba(255,255,255,.06); font-size:13px; font-weight:700; }}
    .layout {{ display:grid; grid-template-columns: 380px minmax(0,1fr); gap:{style["gap_size"]}; }}
    .panel {{ padding:{style["panel_pad"]}; }}
    .panel h2 {{ margin:0 0 14px; font-size:18px; }}
    .stack {{ display:grid; gap:12px; }}
    input, textarea, select {{ width:100%; border-radius:12px; border:1px solid rgba(255,255,255,.08); background:#0a1320; color:var(--text); padding:12px; font:inherit; }}
    button {{ border:0; border-radius:12px; padding:12px 14px; font:inherit; cursor:pointer; }}
    .primary {{ background:linear-gradient(135deg,var(--accent),#ff8a5d); color:white; font-weight:700; }}
    .secondary {{ background:#162334; color:var(--text); border:1px solid rgba(255,255,255,.06); }}
    .status {{ font-size:13px; color:var(--muted); min-height:18px; }}
    .task-list {{ display:grid; gap:12px; }}
    .task {{ border:1px solid rgba(255,255,255,.06); border-radius:16px; padding:14px; background:var(--panel-2); }}
    .task.completed {{ opacity:.72; border-color:rgba(87,209,140,.28); }}
    .task-title {{ font-weight:700; font-size:16px; }}
    .task-meta {{ margin-top:6px; color:var(--muted); font-size:12px; }}
    .task-notes {{ margin-top:10px; color:var(--text); line-height:1.6; white-space:pre-wrap; }}
    .row {{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
    .stats {{ display:grid; grid-template-columns: repeat(3,1fr); gap:12px; }}
    .stat {{ background:var(--panel-2); border:1px solid rgba(255,255,255,.06); border-radius:16px; padding:14px; }}
    .stat-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
    .stat-value {{ margin-top:8px; font-size:28px; font-weight:700; }}
    .empty {{ color:var(--muted); }}
    @media (max-width:980px) {{ .layout {{ grid-template-columns:1fr; }} .stats {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Generated Operational Workspace</div>
      <h1>{app_name}</h1>
      <div class="sub">{description}</div>
      {'' if _copy_key(spec.get('summary', '')) == _copy_key(description) else f'<div class="sub" style="margin-top:8px;">{spec.get("summary", "")}</div>'}
{_workspace_actions(slug)}
    </section>
    <section class="layout">
      <div class="stack">
        <section class="panel">
          <h2>Add Task</h2>
          <div class="stack">
            <input id="task-title" placeholder="Task title" />
            <textarea id="task-notes" rows="4" placeholder="Notes, context, or carry-forward details"></textarea>
            <input id="task-due" placeholder="When should this happen? e.g. Next watch / 1600 / Before brief" />
            <div class="row">
              <button class="primary" id="task-add-btn">Add Task</button>
              <button class="secondary" id="task-carry-btn">Carry Forward Open Tasks</button>
            </div>
            <div style="height:8px;border-radius:999px;background:rgba(255,255,255,.08);overflow:hidden;">
              <div id="task-progress" style="height:100%;width:0%;background:linear-gradient(90deg,var(--accent),#ff8a5d);"></div>
            </div>
            <div class="status" id="task-status">Ready.</div>
            <div class="status" id="task-eta" style="font-size:11px;"></div>
          </div>
        </section>
        <section class="panel">
          <h2>Task Summary</h2>
          <div class="stats">
            <div class="stat"><div class="stat-label">Open</div><div class="stat-value" id="stat-open">0</div></div>
            <div class="stat"><div class="stat-label">Completed</div><div class="stat-value" id="stat-complete">0</div></div>
            <div class="stat"><div class="stat-label">Carry Forward</div><div class="stat-value" id="stat-carry">0</div></div>
          </div>
        </section>
      </div>
      <section class="panel">
        <h2>Daily Task Board</h2>
        <div class="task-list" id="task-list"></div>
      </section>
    </section>
  </div>
  <script>
    const slug = {json.dumps(slug)};
    function esc(text) {{ return String(text ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
    {_workspace_close_script()}
    function setProgress(pct, status, eta='') {{
      document.getElementById('task-progress').style.width = `${{pct}}%`;
      document.getElementById('task-status').textContent = status;
      document.getElementById('task-eta').textContent = eta;
    }}
    function renderTasks(tasks) {{
      const open = tasks.filter(t => !t.completed).length;
      const completed = tasks.filter(t => t.completed).length;
      const carry = tasks.filter(t => !t.completed && /carry|next watch|tomorrow/i.test((t.notes || '') + ' ' + (t.due_label || ''))).length;
      document.getElementById('stat-open').textContent = open;
      document.getElementById('stat-complete').textContent = completed;
      document.getElementById('stat-carry').textContent = carry;
      document.getElementById('task-list').innerHTML = tasks.length ? tasks.map(task => `
        <div class="task ${{task.completed ? 'completed' : ''}}">
          <div class="task-title">${{esc(task.title)}}</div>
          <div class="task-meta">${{esc(task.due_label || 'No timing set')}} · ${{task.completed ? 'Completed' : 'Open'}} · ${{esc(task.source || 'manual')}}</div>
          <div class="task-notes">${{esc(task.notes || 'No notes provided.')}}</div>
          <div class="row" style="margin-top:12px;">
            <button class="secondary" onclick="toggleTask('${{esc(task.id)}}', ${{task.completed ? 'false' : 'true'}})">${{task.completed ? 'Reopen' : 'Complete'}}</button>
            <button class="secondary" onclick="editTaskPrompt('${{esc(task.id)}}', '${{esc(task.title)}}', '${{esc(task.notes || '')}}', '${{esc(task.due_label || '')}}')">Edit</button>
            <button class="secondary" onclick="deleteTask('${{esc(task.id)}}')">Delete</button>
          </div>
        </div>
      `).join('') : '<div class="empty">No tasks yet. Add the missed actions from yesterday and turn them into a daily list.</div>';
    }}
    async function loadWorkspace() {{
      const response = await fetch(`/api/apps/${{slug}}/workspace`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load workspace');
      renderTasks(data.tasks || []);
    }}
    async function addTask() {{
      const title = document.getElementById('task-title').value.trim();
      const notes = document.getElementById('task-notes').value.trim();
      const due = document.getElementById('task-due').value.trim();
      if (!title) {{
        setProgress(0, 'Task title is required.');
        return;
      }}
      setProgress(20, 'Adding task...', '~3s remaining');
      const response = await fetch(`/api/apps/${{slug}}/tasks`, {{
        method:'POST', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ title, notes, due_label: due }})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Add task failed');
      document.getElementById('task-title').value = '';
      document.getElementById('task-notes').value = '';
      document.getElementById('task-due').value = '';
      setProgress(100, 'Task added.', 'Completed');
      await loadWorkspace();
    }}
    async function toggleTask(taskId, completed) {{
      setProgress(40, completed ? 'Marking task complete...' : 'Reopening task...', '~2s remaining');
      const response = await fetch(`/api/apps/${{slug}}/tasks/${{encodeURIComponent(taskId)}}`, {{
        method:'PATCH', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ completed }})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Task update failed');
      setProgress(100, completed ? 'Task completed.' : 'Task reopened.', 'Completed');
      await loadWorkspace();
    }}
    async function deleteTask(taskId) {{
      const response = await fetch(`/api/apps/${{slug}}/tasks/${{encodeURIComponent(taskId)}}`, {{ method:'DELETE' }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Task delete failed');
      await loadWorkspace();
      setProgress(100, 'Task deleted.', 'Completed');
    }}
    async function editTaskPrompt(taskId, currentTitle, currentNotes, currentDue) {{
      const title = window.prompt('Update task title', currentTitle);
      if (title === null) return;
      const notes = window.prompt('Update notes', currentNotes);
      if (notes === null) return;
      const due = window.prompt('Update due label', currentDue);
      if (due === null) return;
      setProgress(35, 'Updating task...', '~2s remaining');
      const response = await fetch(`/api/apps/${{slug}}/tasks/${{encodeURIComponent(taskId)}}`, {{
        method:'PATCH', headers:{{'Content-Type':'application/json'}},
        body: JSON.stringify({{ title, notes, due_label: due }})
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Task update failed');
      setProgress(100, 'Task updated.', 'Completed');
      await loadWorkspace();
    }}
    async function carryForward() {{
      setProgress(25, 'Carrying forward open tasks...', '~3s remaining');
      const response = await fetch(`/api/apps/${{slug}}/tasks/carry-forward`, {{ method:'POST' }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Carry-forward failed');
      setProgress(100, `Created ${{data.created}} carry-forward task(s).`, 'Completed');
      await loadWorkspace();
    }}
    document.getElementById('task-add-btn').addEventListener('click', () => addTask().catch(err => setProgress(100, err.message, 'Failed')));
    document.getElementById('task-carry-btn').addEventListener('click', () => carryForward().catch(err => setProgress(100, err.message, 'Failed')));
    loadWorkspace().catch(err => setProgress(100, err.message, 'Failed'));
  </script>
</body>
</html>"""


def _render_navigation_app_html(slug: str, app_name: str, description: str, spec: dict) -> str:
    style = _style_tokens(spec)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{app_name} - WarClaw</title>
  <style>
    :root {{
      --bg: {style["bg"]};
      --bg-alt: {style["bg_alt"]};
      --panel: {style["panel"]};
      --panel-2: {style["panel_2"]};
      --border: {style["border"]};
      --text: {style["text"]};
      --muted: {style["muted"]};
      --accent: {style["accent"]};
      --accent-2: {style["accent_2"]};
      --good: #57d18c;
      --warn: #ffc857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(101, 216, 255, 0.12), transparent 28%),
        radial-gradient(circle at top left, rgba(255, 111, 59, 0.10), transparent 24%),
        linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{ max-width: {style["shell_width"]}; margin: 0 auto; padding: 28px; }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: {style["radius_size"]};
    }}
    .hero {{ padding: {style["hero_pad"]}; margin-bottom: 18px; }}
    .eyebrow {{ color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.18em; font-size: 11px; }}
    h1 {{ margin: 10px 0 8px; font-size: 38px; }}
    .sub {{ color: var(--muted); line-height: 1.7; max-width: 940px; }}
    .layout {{ display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: {style["gap_size"]}; }}
    .stack {{ display: grid; gap: {style["gap_size"]}; }}
    .panel {{ padding: {style["panel_pad"]}; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
    .stat {{
      background: var(--panel-2);
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 16px;
      padding: 14px;
    }}
    .stat-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .stat-value {{ margin-top: 8px; font-size: 28px; font-weight: 700; }}
    .row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .hero-actions {{ margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .hero-link {{
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 12px; padding: 10px 14px; text-decoration: none;
      background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06);
      font-size: 13px; font-weight: 700;
    }}
    button {{
      border: 0;
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      cursor: pointer;
    }}
    .primary {{ background: linear-gradient(135deg, var(--accent), #ff8a5d); color: white; font-weight: 700; }}
    .secondary {{ background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06); }}
    .status {{ color: var(--muted); min-height: 18px; font-size: 13px; }}
    .frame-list, .source-list {{ display: grid; gap: 12px; max-height: 620px; overflow: auto; }}
    .frame, .source {{
      background: var(--panel-2);
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 16px;
      padding: 14px;
    }}
    .frame-title, .source-title {{ font-weight: 700; font-size: 15px; }}
    .meta {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .decoded {{ margin-top: 10px; color: var(--text); font-size: 13px; line-height: 1.6; white-space: pre-wrap; }}
    .pill {{ display: inline-block; margin-right: 8px; margin-top: 8px; padding: 6px 10px; border-radius: 999px; background: rgba(101,216,255,0.12); color: var(--accent-2); font-size: 12px; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 1080px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: repeat(2, 1fr); }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Generated Operational Workspace</div>
      <h1>{app_name}</h1>
      <div class="sub">{description}</div>
      {'' if _copy_key(spec.get('summary', '')) == _copy_key(description) else f'<div class="sub" style="margin-top:8px;">{spec.get("summary", "")}</div>'}
{_workspace_actions(slug)}
    </section>

    <section class="layout">
      <div class="stack">
        <section class="panel">
          <div class="stats">
            <div class="stat">
              <div class="stat-label">Position</div>
              <div class="stat-value" id="stat-position">-</div>
            </div>
            <div class="stat">
              <div class="stat-label">Heading</div>
              <div class="stat-value" id="stat-heading">-</div>
            </div>
            <div class="stat">
              <div class="stat-label">Speed</div>
              <div class="stat-value" id="stat-speed">-</div>
            </div>
            <div class="stat">
              <div class="stat-label">Depth</div>
              <div class="stat-value" id="stat-depth">-</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="eyebrow">Capture Control</div>
          <h2 style="margin:8px 0 12px;">Live Feed</h2>
          <div class="row">
            <button class="primary" id="start-capture-btn">Start Capture</button>
            <button class="secondary" id="stop-capture-btn">Stop Capture</button>
            <button class="secondary" id="refresh-btn">Refresh</button>
          </div>
          <div style="margin-top:12px;height:8px;border-radius:999px;background:rgba(255,255,255,0.08);overflow:hidden;">
            <div id="capture-progress" style="height:100%;width:0%;background:linear-gradient(90deg, var(--accent), #ff8a5d);"></div>
          </div>
          <div class="status" id="capture-status" style="margin-top:12px;">Ready.</div>
          <div class="status" id="capture-eta" style="font-size:11px;"></div>
        </section>

        <section class="panel">
          <div class="eyebrow">Recent Navigation Frames</div>
          <h2 style="margin:8px 0 12px;">Decoded Feed</h2>
          <div class="frame-list" id="frame-list"></div>
        </section>
      </div>

      <div class="stack">
        <section class="panel">
          <div class="eyebrow">Candidate Sources</div>
          <h2 style="margin:8px 0 12px;">NMEA / Bridge Systems</h2>
          <div class="source-list" id="source-list"></div>
        </section>
      </div>
    </section>
  </div>

  <script>
    const slug = {json.dumps(slug)};

    function esc(text) {{
      return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }}

    {_workspace_close_script()}

    function fmtNumber(value, suffix='') {{
      return value == null ? '-' : `${{value}}${{suffix}}`;
    }}

    function setProgress(fillId, statusId, etaId, pct, statusText, etaText='') {{
      const fill = document.getElementById(fillId);
      const status = document.getElementById(statusId);
      const eta = document.getElementById(etaId);
      if (fill) fill.style.width = `${{pct}}%`;
      if (status) status.textContent = statusText;
      if (eta) eta.textContent = etaText;
    }}

    function render(data) {{
      const nav = data.navigation || {{}};
      document.getElementById('stat-position').textContent = nav.position || '-';
      document.getElementById('stat-heading').textContent = fmtNumber(nav.heading_deg, '°');
      document.getElementById('stat-speed').textContent = fmtNumber(nav.speed_knots, ' kn');
      document.getElementById('stat-depth').textContent = fmtNumber(nav.depth_m, ' m');

      const frames = data.navigation_frames || [];
      document.getElementById('frame-list').innerHTML = frames.length
        ? frames.map(frame => `
            <div class="frame">
              <div class="frame-title">${{esc(frame.sentence_type || frame.type || 'NMEA')}} · ${{esc(frame.source || 'unknown source')}}</div>
              <div class="meta">${{new Date((frame.timestamp || 0) * 1000).toLocaleString()}}${{frame.checksum_ok === false ? ' · checksum failed' : ''}}</div>
              <div class="decoded">${{esc(frame.summary || '')}}</div>
            </div>
          `).join('')
        : '<div class="empty">No decoded navigation frames yet. Start capture or stream a live NMEA source.</div>';

      const sources = data.navigation_sources || [];
      document.getElementById('source-list').innerHTML = sources.length
        ? sources.map(source => `
            <div class="source">
              <div class="source-title">${{esc(source.ip)}}${{source.hostname ? ` · ${{esc(source.hostname)}}` : ''}}</div>
              <div class="meta">${{esc(source.protocols.join(', ') || 'unknown protocol')}}</div>
              ${{(source.ports || []).map(port => `<span class="pill">${{esc(port)}}</span>`).join('')}}
              <div class="decoded">${{esc((source.integration_hints || []).join('\\n'))}}</div>
            </div>
          `).join('')
        : '<div class="empty">No candidate navigation sources detected yet. Run a LAN scan first.</div>';
    }}

    async function refreshWorkspace() {{
      const response = await fetch(`/api/apps/${{slug}}/data`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load workspace');
      render(data);
    }}

    async function startCapture() {{
      setProgress('capture-progress', 'capture-status', 'capture-eta', 18, 'Starting traffic capture...', '~5s remaining');
      const response = await fetch('/api/traffic/start', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ interface: '', duration: 0 }}),
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Capture start failed');
      setProgress('capture-progress', 'capture-status', 'capture-eta', 100, data.status === 'already_running' ? 'Capture already running.' : 'Traffic capture started.', 'Live');
      await refreshWorkspace();
    }}

    async function stopCapture() {{
      setProgress('capture-progress', 'capture-status', 'capture-eta', 72, 'Stopping traffic capture...', '~3s remaining');
      const response = await fetch('/api/traffic/stop', {{ method: 'POST' }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Capture stop failed');
      setProgress('capture-progress', 'capture-status', 'capture-eta', 0, 'Traffic capture stopped.', '');
      await refreshWorkspace();
    }}

    document.getElementById('start-capture-btn').addEventListener('click', () => {{
      startCapture().catch(err => {{
        setProgress('capture-progress', 'capture-status', 'capture-eta', 100, err.message, 'Failed');
      }});
    }});
    document.getElementById('stop-capture-btn').addEventListener('click', () => {{
      stopCapture().catch(err => {{
        setProgress('capture-progress', 'capture-status', 'capture-eta', 100, err.message, 'Failed');
      }});
    }});
    document.getElementById('refresh-btn').addEventListener('click', () => {{
      refreshWorkspace().catch(err => {{
        setProgress('capture-progress', 'capture-status', 'capture-eta', 100, err.message, 'Failed');
      }});
    }});

    refreshWorkspace().catch(err => {{
      setProgress('capture-progress', 'capture-status', 'capture-eta', 100, err.message, 'Failed');
    }});
  </script>
</body>
</html>"""


def _extract_navigation_state(frames: list[dict]) -> tuple[dict, list[dict]]:
    nav = {
        "position": None,
        "latitude": None,
        "longitude": None,
        "heading_deg": None,
        "speed_knots": None,
        "depth_m": None,
        "course_deg": None,
        "last_sentence_type": None,
        "last_update": None,
    }
    decoded_frames = []

    for frame in reversed(frames):
        if frame.get("content_type") != "nmea":
            continue
        decoded = frame.get("payload_decoded") or {}
        data = decoded.get("data") or {}
        sentence_type = decoded.get("type") or ""
        summary_parts = []

        if nav["latitude"] is None and data.get("latitude") is not None:
            nav["latitude"] = data.get("latitude")
        if nav["longitude"] is None and data.get("longitude") is not None:
            nav["longitude"] = data.get("longitude")
        if nav["heading_deg"] is None and data.get("heading_deg") is not None:
            nav["heading_deg"] = data.get("heading_deg")
        if nav["speed_knots"] is None and data.get("speed_knots") is not None:
            nav["speed_knots"] = data.get("speed_knots")
        if nav["depth_m"] is None and data.get("depth_m") is not None:
            nav["depth_m"] = data.get("depth_m")
        if nav["course_deg"] is None and data.get("course_deg") is not None:
            nav["course_deg"] = data.get("course_deg")

        if data.get("latitude") is not None and data.get("longitude") is not None:
            summary_parts.append(f"Position {data['latitude']}, {data['longitude']}")
        if data.get("heading_deg") is not None:
            summary_parts.append(f"Heading {data['heading_deg']} deg")
        if data.get("speed_knots") is not None:
            summary_parts.append(f"Speed {data['speed_knots']} kn")
        if data.get("depth_m") is not None:
            summary_parts.append(f"Depth {data['depth_m']} m")
        if data.get("status"):
            summary_parts.append(f"Status {data['status']}")

        decoded_frames.append({
            "source": frame.get("src"),
            "timestamp": frame.get("timestamp"),
            "sentence_type": sentence_type,
            "checksum_ok": decoded.get("checksum_ok"),
            "summary": "; ".join(summary_parts) or frame.get("payload_preview", "")[:200],
            "type": data.get("type"),
        })

    if nav["latitude"] is not None and nav["longitude"] is not None:
        nav["position"] = f"{nav['latitude']}, {nav['longitude']}"
    if decoded_frames:
        nav["last_sentence_type"] = decoded_frames[0].get("sentence_type")
        nav["last_update"] = decoded_frames[0].get("timestamp")

    return nav, decoded_frames[:12]


def _extract_navigation_sources(scan: Optional[dict]) -> list[dict]:
    if not scan:
        return []
    sources = []
    for host in scan.get("hosts", []):
        services = host.get("services", [])
        nav_services = [service for service in services if "nmea" in (service.get("protocol") or "").lower() or service.get("port") in {10110, 2000, 4001, 3960, 20000}]
        if not nav_services:
            continue
        sources.append({
            "ip": host.get("ip"),
            "hostname": host.get("hostname"),
            "ports": [f"{service.get('port')}/{service.get('protocol')}" for service in nav_services],
            "protocols": sorted({service.get("protocol") or "unknown" for service in nav_services}),
            "integration_hints": host.get("integration_hints", []),
        })
    return sources[:12]


def _render_lan_app_html(slug: str, app_name: str, description: str, spec: dict) -> str:
    style = _style_tokens(spec)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{app_name} - WarClaw</title>
  <style>
    :root {{
      --bg: {style["bg"]};
      --bg-alt: {style["bg_alt"]};
      --panel: {style["panel"]};
      --panel-2: {style["panel_2"]};
      --border: {style["border"]};
      --text: {style["text"]};
      --muted: {style["muted"]};
      --accent: {style["accent"]};
      --accent-2: {style["accent_2"]};
      --good: #57d18c;
      --warn: #ffc857;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(101, 216, 255, 0.12), transparent 28%),
        radial-gradient(circle at top left, rgba(255, 111, 59, 0.10), transparent 24%),
        linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{ max-width: {style["shell_width"]}; margin: 0 auto; padding: 28px; }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: {style["radius_size"]};
    }}
    .hero {{ padding: {style["hero_pad"]}; margin-bottom: 18px; }}
    .eyebrow {{ color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.18em; font-size: 11px; }}
    h1 {{ margin: 10px 0 8px; font-size: 38px; }}
    .sub {{ color: var(--muted); line-height: 1.7; max-width: 940px; }}
    .layout {{ display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: {style["gap_size"]}; }}
    .stack {{ display: grid; gap: {style["gap_size"]}; }}
    .panel {{ padding: {style["panel_pad"]}; }}
    .stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .stat {{
      background: var(--panel-2);
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 16px;
      padding: 14px;
    }}
    .stat-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .stat-value {{ margin-top: 8px; font-size: 28px; font-weight: 700; }}
    .row {{ display: flex; gap: 10px; align-items: center; }}
    .hero-actions {{ margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .hero-link {{
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 12px; padding: 10px 14px; text-decoration: none;
      background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06);
      font-size: 13px; font-weight: 700;
    }}
    input {{
      width: 100%;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.08);
      background: #0a1320;
      color: var(--text);
      padding: 12px;
      font: inherit;
    }}
    button, .linkbtn {{
      border: 0;
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }}
    .primary {{ background: linear-gradient(135deg, var(--accent), #ff8a5d); color: white; font-weight: 700; }}
    .secondary {{ background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06); }}
    .status {{ color: var(--muted); min-height: 18px; font-size: 13px; }}
    .host-list, .rec-list {{ display: grid; gap: 12px; max-height: 680px; overflow: auto; }}
    .host, .rec {{
      background: var(--panel-2);
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 16px;
      padding: 14px;
    }}
    .host-title {{ font-weight: 700; font-size: 16px; }}
    .meta {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .pills {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px; }}
    .pill {{ padding: 6px 10px; border-radius: 999px; background: rgba(101,216,255,0.12); color: var(--accent-2); font-size: 12px; }}
    .hint {{ margin-top: 10px; color: var(--muted); line-height: 1.6; white-space: pre-wrap; }}
    .empty {{ color: var(--muted); }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
      .stats {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Generated Operational Workspace</div>
      <h1>{app_name}</h1>
      <div class="sub">{description}</div>
      {'' if _copy_key(spec.get('summary', '')) == _copy_key(description) else f'<div class="sub" style="margin-top:8px;">{spec.get("summary", "")}</div>'}
{_workspace_actions(slug)}
    </section>

    <section class="layout">
      <div class="stack">
        <section class="panel">
          <div class="eyebrow">Scan Controls</div>
          <h2 style="margin:8px 0 12px;">Run Discovery</h2>
          <input id="network-input" placeholder="CIDR network, e.g. 192.168.1.0/24 (leave blank for auto-detect)" />
          <div class="row" style="margin-top:12px;">
            <button class="primary" id="scan-btn">Run Scan</button>
            <a class="linkbtn secondary" href="/api/lan/scan/export" target="_blank" rel="noopener">Export JSON</a>
          </div>
          <div style="margin-top:12px;height:8px;border-radius:999px;background:rgba(255,255,255,0.08);overflow:hidden;">
            <div id="scan-progress" style="height:100%;width:0%;background:linear-gradient(90deg, var(--accent), #ff8a5d);"></div>
          </div>
          <div class="status" id="scan-status" style="margin-top:12px;">Ready.</div>
          <div class="status" id="scan-eta" style="font-size:11px;"></div>
        </section>

        <section class="panel">
          <div class="eyebrow">Recommendations</div>
          <h2 style="margin:8px 0 12px;">Suggested Actions</h2>
          <div class="rec-list" id="rec-list"></div>
        </section>
      </div>

      <div class="stack">
        <section class="panel">
          <div class="stats">
            <div class="stat">
              <div class="stat-label">Network</div>
              <div class="stat-value" id="stat-network">-</div>
            </div>
            <div class="stat">
              <div class="stat-label">Hosts Up</div>
              <div class="stat-value" id="stat-hosts">0</div>
            </div>
            <div class="stat">
              <div class="stat-label">Scan Time</div>
              <div class="stat-value" id="stat-duration">0s</div>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="eyebrow">Discovered Hosts</div>
          <h2 style="margin:8px 0 12px;">Operational Footprint</h2>
          <div class="host-list" id="host-list"></div>
        </section>
      </div>
    </section>
  </div>

  <script>
    const slug = {json.dumps(slug)};

    function esc(text) {{
      return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }}

    {_workspace_close_script()}

    function setProgress(pct, statusText, etaText='') {{
      document.getElementById('scan-progress').style.width = `${{pct}}%`;
      document.getElementById('scan-status').textContent = statusText;
      document.getElementById('scan-eta').textContent = etaText;
    }}

    function render(data) {{
      const scan = data.lan_overview || null;
      document.getElementById('stat-network').textContent = scan?.network || '-';
      document.getElementById('stat-hosts').textContent = scan?.hosts_up || 0;
      document.getElementById('stat-duration').textContent = scan ? `${{scan.scan_duration_s}}s` : '0s';

      const recs = scan?.recommendations || [];
      document.getElementById('rec-list').innerHTML = recs.length
        ? recs.map(item => `<div class="rec">${{esc(item)}}</div>`).join('')
        : '<div class="empty">No recommendations yet. Run a scan to generate operational suggestions.</div>';

      const hosts = scan?.hosts || [];
      document.getElementById('host-list').innerHTML = hosts.length
        ? hosts.map(host => `
            <div class="host">
              <div class="host-title">${{esc(host.ip)}}${{host.hostname ? ` · ${{esc(host.hostname)}}` : ''}}</div>
              <div class="meta">${{(host.open_ports || []).length}} open port(s)</div>
              <div class="pills">
                ${{(host.services || []).map(service => `<span class="pill">${{esc(service.port)}}/${{esc(service.protocol)}}</span>`).join('') || '<span class="pill">No services</span>'}}
              </div>
              <div class="hint">${{esc((host.integration_hints || []).join('\\n'))}}</div>
            </div>
          `).join('')
        : '<div class="empty">No scan data persisted yet.</div>';
    }}

    async function refreshWorkspace() {{
      const response = await fetch(`/api/apps/${{slug}}/data`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load workspace');
      render(data);
    }}

    async function runScan() {{
      const network = document.getElementById('network-input').value.trim();
      setProgress(16, network ? `Scanning ${{network}}...` : 'Scanning network...', '~30s remaining');
      const url = network ? `/api/lan/scan?network=${{encodeURIComponent(network)}}` : '/api/lan/scan';
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Scan failed');
      setProgress(100, `Scan complete: ${{data.hosts_up}} host(s) on ${{data.network}} in ${{data.scan_duration_s}}s.`, 'Completed');
      await refreshWorkspace();
    }}

    document.getElementById('scan-btn').addEventListener('click', () => {{
      runScan().catch(err => {{
        setProgress(100, err.message, 'Failed');
      }});
    }});

    refreshWorkspace().catch(err => {{
      setProgress(100, err.message, 'Failed');
    }});
  </script>
</body>
</html>"""


def _render_report_app_html(slug: str, app_name: str, description: str, spec: dict) -> str:
    style = _style_tokens(spec)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{app_name} - WarClaw</title>
  <style>
    :root {{
      --bg: {style["bg"]};
      --bg-alt: {style["bg_alt"]};
      --panel: {style["panel"]};
      --panel-2: {style["panel_2"]};
      --border: {style["border"]};
      --text: {style["text"]};
      --muted: {style["muted"]};
      --accent: {style["accent"]};
      --accent-2: {style["accent_2"]};
      --good: #57d18c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top right, rgba(101, 216, 255, 0.12), transparent 28%),
        radial-gradient(circle at top left, rgba(255, 111, 59, 0.12), transparent 24%),
        linear-gradient(180deg, var(--bg-alt) 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{ max-width: {style["shell_width"]}; margin: 0 auto; padding: 28px; }}
    .hero, .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: {style["radius_size"]};
    }}
    .hero {{ padding: {style["hero_pad"]}; margin-bottom: 18px; }}
    .eyebrow {{ color: var(--accent-2); text-transform: uppercase; letter-spacing: 0.18em; font-size: 11px; }}
    h1 {{ margin: 10px 0 8px; font-size: 40px; }}
    .sub {{ color: var(--muted); max-width: 920px; line-height: 1.7; }}
    .layout {{ display: grid; grid-template-columns: 360px minmax(0, 1fr); gap: {style["gap_size"]}; align-items: start; }}
    .panel {{ padding: {style["panel_pad"]}; }}
    .panel h2 {{ margin: 0 0 14px; font-size: 18px; }}
    .stack {{ display: grid; gap: 12px; }}
    .dropzone {{
      border: 1px dashed rgba(101, 216, 255, 0.34);
      border-radius: 16px;
      padding: 18px;
      background: rgba(255,255,255,0.02);
    }}
    .dropzone input, textarea, select {{
      width: 100%;
      border-radius: 12px;
      border: 1px solid rgba(255,255,255,0.08);
      background: #0a1320;
      color: var(--text);
      padding: 12px;
      font: inherit;
    }}
    .row {{ display: flex; gap: 10px; align-items: center; }}
    .hero-actions {{ margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap; }}
    .hero-link {{
      display: inline-flex; align-items: center; justify-content: center;
      border-radius: 12px; padding: 10px 14px; text-decoration: none;
      background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06);
      font-size: 13px; font-weight: 700;
    }}
    button {{
      border: 0;
      border-radius: 12px;
      padding: 12px 14px;
      font: inherit;
      cursor: pointer;
    }}
    .primary {{ background: linear-gradient(135deg, var(--accent), #ff8a5d); color: white; font-weight: 700; }}
    .secondary {{ background: #162334; color: var(--text); border: 1px solid rgba(255,255,255,0.06); }}
    .status {{ font-size: 13px; color: var(--muted); min-height: 18px; }}
    .file-list {{ display: grid; gap: 10px; max-height: 320px; overflow: auto; }}
    .report-list {{ display: grid; gap: 10px; max-height: 240px; overflow: auto; }}
    .file {{
      border: 1px solid rgba(255,255,255,0.06);
      border-radius: 14px;
      padding: 12px;
      background: var(--panel-2);
    }}
    .file-name {{ font-weight: 700; }}
    .file-meta {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
    .report-meta {{ margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.5; }}
    .report-card {{
      min-height: 520px;
      display: grid;
      gap: 18px;
      align-content: start;
    }}
    .summary {{
      border-left: 3px solid var(--accent);
      padding-left: 14px;
      color: var(--text);
      line-height: 1.7;
    }}
    .section {{
      border-top: 1px solid rgba(255,255,255,0.06);
      padding-top: 14px;
    }}
    .section h3 {{ margin: 0 0 8px; font-size: 15px; }}
    .section p {{ margin: 0; color: var(--muted); line-height: 1.7; white-space: pre-wrap; }}
    .pill {{
      display: inline-block;
      margin-right: 8px;
      margin-bottom: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(87, 209, 140, 0.12);
      color: var(--good);
      font-size: 12px;
    }}
    .empty {{ color: var(--muted); font-size: 14px; }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="eyebrow">Generated Operational Workspace</div>
      <h1>{app_name}</h1>
      <div class="sub">{description}</div>
      {'' if _copy_key(spec.get('summary', '')) == _copy_key(description) else f'<div class="sub" style="margin-top:8px;">{spec.get("summary", "")}</div>'}
{_workspace_actions(slug)}
    </section>

    <section class="layout">
      <div class="stack">
        <section class="panel">
          <h2>Source Files</h2>
          <div class="dropzone">
            <input id="file-input" type="file" multiple />
            <div class="status" style="margin-top:10px;">Upload `.txt`, `.md`, `.json`, `.csv`, `.log`, `.xml`, or converted plain-text documents.</div>
            <div class="row" style="margin-top:12px;">
              <button class="primary" id="upload-btn">Upload Files</button>
              <button class="secondary" id="refresh-btn">Refresh</button>
            </div>
            <div style="margin-top:12px;height:8px;border-radius:999px;background:rgba(255,255,255,0.08);overflow:hidden;">
              <div id="upload-progress" style="height:100%;width:0%;background:linear-gradient(90deg, var(--accent), #ff8a5d);"></div>
            </div>
            <div class="status" id="upload-status" style="margin-top:12px;"></div>
            <div class="status" id="upload-eta" style="font-size:11px;"></div>
          </div>
          <div class="file-list" id="file-list" style="margin-top:14px;"></div>
        </section>

        <section class="panel">
          <h2>Saved Reports</h2>
          <div class="report-list" id="report-list"></div>
        </section>

        <section class="panel">
          <h2>Report Controls</h2>
          <div class="stack">
            <select id="report-type">
              <option value="daily_opord_summary">Daily OPORD Summary</option>
              <option value="uploaded_report">Uploaded File Summary</option>
              <option value="watch_brief">Watch Brief</option>
              <option value="executive_brief">Executive Brief</option>
            </select>
            <textarea id="report-prompt" rows="6" placeholder="Describe the report you want. Example: Summarize these uploaded notes into a daily OPORD with risks, decisions, and next actions."></textarea>
            <button class="primary" id="generate-btn">Generate Report</button>
            <div style="height:8px;border-radius:999px;background:rgba(255,255,255,0.08);overflow:hidden;">
              <div id="report-progress" style="height:100%;width:0%;background:linear-gradient(90deg, var(--accent), #ff8a5d);"></div>
            </div>
            <div class="status" id="report-status"></div>
            <div class="status" id="report-eta" style="font-size:11px;"></div>
          </div>
        </section>
      </div>

      <section class="panel report-card">
        <div>
          <div class="eyebrow">Output</div>
          <h2 style="margin-top:8px;" id="report-title">No report generated yet</h2>
          <div class="summary" id="report-summary">Upload files, then generate a report.</div>
        </div>
        <div id="report-sections"></div>
        <div>
          <div class="eyebrow" style="margin-bottom:10px;">Recommended Actions</div>
          <div id="report-actions" class="empty">No actions yet.</div>
        </div>
        <div class="row">
          <button class="secondary" id="download-latest-btn">Download Latest Markdown</button>
        </div>
      </section>
    </section>
  </div>

  <script>
    const slug = {json.dumps(slug)};

    function esc(text) {{
      return String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }}

    {_workspace_close_script()}

    function setProgress(fillId, statusId, etaId, pct, statusText, etaText='') {{
      document.getElementById(fillId).style.width = `${{pct}}%`;
      document.getElementById(statusId).textContent = statusText;
      document.getElementById(etaId).textContent = etaText;
    }}

    async function loadWorkspace() {{
      const response = await fetch(`/api/apps/${{slug}}/workspace`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load workspace');
      const files = data.files || [];
      const reports = data.reports || [];
      const list = document.getElementById('file-list');
      list.innerHTML = files.length
        ? files.map(file => `
            <div class="file">
              <div class="file-name">${{esc(file.name)}}</div>
              <div class="file-meta">${{esc(file.suffix || 'unknown')}} · ${{Math.round((file.size_bytes || 0) / 1024)}} KB</div>
              <div class="row" style="margin-top:10px;">
                <button class="secondary" onclick="deleteFile('${{esc(file.name)}}')">Delete</button>
              </div>
            </div>
          `).join('')
        : '<div class="empty">No uploaded files yet.</div>';

      const reportList = document.getElementById('report-list');
      reportList.innerHTML = reports.length
        ? reports.map(report => `
            <div class="file">
              <div class="file-name">${{esc(report.title)}}</div>
              <div class="report-meta">${{new Date((report.generated_at || 0) * 1000).toLocaleString()}}<br>${{esc(report.summary || '').slice(0, 160)}}</div>
              <div class="row" style="margin-top:10px;">
                <button class="secondary" onclick="downloadReport('${{esc(report.id)}}')">Markdown</button>
                <button class="secondary" onclick="deleteReport('${{esc(report.id)}}')">Delete</button>
              </div>
            </div>
          `).join('')
        : '<div class="empty">No saved reports yet.</div>';

      if (data.latest_report) {{
        renderReport(data.latest_report);
      }}
    }}

    async function uploadFiles() {{
      const input = document.getElementById('file-input');
      if (!input.files.length) {{
        setProgress('upload-progress', 'upload-status', 'upload-eta', 0, 'Choose at least one file first.', '');
        return;
      }}
      const formData = new FormData();
      Array.from(input.files).forEach(file => formData.append('files', file));
      setProgress('upload-progress', 'upload-status', 'upload-eta', 22, `Uploading ${{input.files.length}} file(s)...`, '~10s remaining');
      const response = await fetch(`/api/apps/${{slug}}/files`, {{ method: 'POST', body: formData }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Upload failed');
      setProgress('upload-progress', 'upload-status', 'upload-eta', 100, `Uploaded ${{data.saved.length}} file(s).`, 'Completed');
      input.value = '';
      await loadWorkspace();
    }}

    function renderReport(report) {{
      document.getElementById('report-title').textContent = report.title || 'Generated Report';
      document.getElementById('report-summary').textContent = report.summary || 'No summary generated.';

      const sections = document.getElementById('report-sections');
      sections.innerHTML = (report.sections || []).length
        ? report.sections.map(section => `
            <div class="section">
              <h3>${{esc(section.heading || 'Section')}}</h3>
              <p>${{esc(section.body || '')}}</p>
            </div>
          `).join('')
        : '<div class="empty">No detailed sections returned.</div>';

      const actions = document.getElementById('report-actions');
      actions.innerHTML = (report.recommended_actions || []).length
        ? report.recommended_actions.map(action => `<span class="pill">${{esc(action)}}</span>`).join('')
        : '<div class="empty">No recommended actions.</div>';
    }}

    async function generateReport() {{
      setProgress('report-progress', 'report-status', 'report-eta', 18, 'Generating report...', '~20s remaining');
      const response = await fetch(`/api/apps/${{slug}}/actions/report-summary`, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{
          report_type: document.getElementById('report-type').value,
          prompt: document.getElementById('report-prompt').value.trim(),
        }}),
      }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Report generation failed');
      setProgress('report-progress', 'report-status', 'report-eta', 100, `Generated at ${{new Date((data.generated_at || Date.now()/1000) * 1000).toLocaleString()}}`, 'Completed');
      renderReport(data);
      await loadWorkspace();
    }}

    function downloadReport(reportId) {{
      window.open(`/api/apps/${{slug}}/reports/${{reportId}}.md`, '_blank', 'noopener');
    }}

    async function deleteFile(name) {{
      const response = await fetch(`/api/apps/${{slug}}/files/${{encodeURIComponent(name)}}`, {{ method: 'DELETE' }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Delete failed');
      await loadWorkspace();
    }}

    async function deleteReport(reportId) {{
      const response = await fetch(`/api/apps/${{slug}}/reports/${{encodeURIComponent(reportId)}}`, {{ method: 'DELETE' }});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Delete failed');
      await loadWorkspace();
    }}

    document.getElementById('refresh-btn').addEventListener('click', () => {{
      loadWorkspace().catch(err => {{
        setProgress('upload-progress', 'upload-status', 'upload-eta', 100, err.message, 'Failed');
      }});
    }});
    document.getElementById('upload-btn').addEventListener('click', () => {{
      uploadFiles().catch(err => {{
        setProgress('upload-progress', 'upload-status', 'upload-eta', 100, err.message, 'Failed');
      }});
    }});
    document.getElementById('generate-btn').addEventListener('click', () => {{
      generateReport().catch(err => {{
        setProgress('report-progress', 'report-status', 'report-eta', 100, err.message, 'Failed');
      }});
    }});
    document.getElementById('download-latest-btn').addEventListener('click', () => {{
      window.open(`/api/apps/${{slug}}/reports/latest.md`, '_blank', 'noopener');
    }});

    loadWorkspace().catch(err => {{
      setProgress('upload-progress', 'upload-status', 'upload-eta', 100, err.message, 'Failed');
    }});
  </script>
</body>
</html>"""


def get_app_spec(slug: str) -> Optional[dict]:
    app_dir = GENERATED_APPS_DIR / slug
    spec_path = GENERATED_APPS_DIR / slug / "spec.json"
    manifest_path = app_dir / "manifest.json"
    if not spec_path.exists():
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            spec = {
                "app_kind": _detect_app_kind(
                    manifest.get("name", slug),
                    manifest.get("description", ""),
                    manifest.get("context", ""),
                ),
                "summary": manifest.get("operator_summary") or manifest.get("description", ""),
                "widgets": _heuristic_widgets(manifest.get("description", ""), manifest.get("context", "")),
            }
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            index_path = app_dir / "index.html"
            index_path.write_text(
                _render_app_html(
                    slug,
                    manifest.get("name", slug),
                    manifest.get("description", ""),
                    spec,
                ),
                encoding="utf-8",
            )
        except Exception:
            return None
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if "app_kind" not in spec and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            spec["app_kind"] = _detect_app_kind(
                manifest.get("name", slug),
                manifest.get("description", ""),
                manifest.get("context", ""),
            )
            spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            (app_dir / "index.html").write_text(
                _render_app_html(
                    slug,
                    manifest.get("name", slug),
                    manifest.get("description", ""),
                    spec,
                ),
                encoding="utf-8",
            )
        except Exception:
            pass
    return spec


def build_app_data(slug: str, spec: dict) -> dict:
    from ..routers.lan import _last_scan_result  # type: ignore

    hardware = detect_hardware()
    recent_frames = traffic_monitor.get_recent_frames(40)
    navigation, navigation_frames = _extract_navigation_state(recent_frames)
    navigation_sources = _extract_navigation_sources(_last_scan_result)
    return {
        "app": {
            "slug": slug,
            "app_kind": spec.get("app_kind", "operational_dashboard"),
            "summary": spec.get("summary", ""),
            "widgets": spec.get("widgets", []),
        },
        "status_summary": {
            "model_ready": llm_service.ready,
            "current_model": llm_service.model_path,
            "generated_app_count": len(list_generated_apps()),
            "agent_count": agent_engine.summary.get("total_agents", 0),
        },
        "lan_overview": _last_scan_result,
        "agents": agent_engine.summary.get("agents", []),
        "agent_summary": agent_engine.summary,
        "traffic": {
            "stats": traffic_monitor.stats,
            "conversations": traffic_monitor.get_conversations()[:20],
            "recent_frames": recent_frames[:20],
        },
        "navigation": navigation,
        "navigation_frames": navigation_frames,
        "navigation_sources": navigation_sources,
        "events": recent(40),
        "generated_apps": list_generated_apps(),
        "hardware": {
            "cpu_cores": hardware.cpu_cores,
            "cpu_model": hardware.cpu_model,
            "ram_gb": hardware.ram_gb,
            "gpu_name": hardware.gpu_name,
            "recommended_tier": hardware.recommended_tier,
            "recommended_model": hardware.recommended_model_hint,
        },
        "chat_sessions": list_sessions(),
        "files": list_app_files(slug),
        "reports": list_app_reports(slug),
        "latest_report": get_latest_app_report(slug),
        "tasks": list_app_tasks(slug),
    }


async def generate_app(app_name: str, description: str, context: str = "") -> dict:
    blueprint = _prepare_blueprint(app_name, description, context)
    slug = _slugify(blueprint["name"])
    app_dir = GENERATED_APPS_DIR / slug
    if app_dir.exists():
        slug = f"{slug}-{int(time.time()) % 10000}"
        app_dir = GENERATED_APPS_DIR / slug
    app_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    spec = await _build_spec(blueprint["name"], blueprint["description"], context, app_kind=blueprint["app_kind"], summary_seed=blueprint["summary"])
    html = _render_app_html(slug, blueprint["name"], blueprint["description"], spec)

    (app_dir / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (app_dir / "index.html").write_text(html, encoding="utf-8")
    (app_dir / "backend.py").write_text(
        "# Generated apps are served through WarClaw's generic /api/apps/{slug}/data endpoint.\n",
        encoding="utf-8",
    )

    manifest = {
        "slug": slug,
        "name": blueprint["name"],
        "description": blueprint["description"],
        "context": context,
        "app_kind": spec.get("app_kind", "operational_dashboard"),
        "operator_summary": spec.get("summary", ""),
        "theme": spec.get("theme", "naval"),
        "density": spec.get("density", "comfortable"),
        "created_at": time.time(),
        "generation_time_s": round(time.time() - t0, 1),
        "status": "success",
        "errors": [],
        "has_backend": True,
        "has_frontend": True,
        "widget_count": len(spec["widgets"]),
    }
    (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


async def update_app(slug: str, app_name: str, description: str, context: str = "") -> dict:
    app_dir = GENERATED_APPS_DIR / slug
    manifest_path = app_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(slug)

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    blueprint = _prepare_blueprint(app_name, description, context)
    t0 = time.time()
    spec = await _build_spec(blueprint["name"], blueprint["description"], context, app_kind=blueprint["app_kind"], summary_seed=blueprint["summary"])
    (app_dir / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    (app_dir / "index.html").write_text(_render_app_html(slug, blueprint["name"], blueprint["description"], spec), encoding="utf-8")
    manifest = {
        **existing,
        "slug": slug,
        "name": blueprint["name"],
        "description": blueprint["description"],
        "context": context,
        "app_kind": spec.get("app_kind", existing.get("app_kind", "operational_dashboard")),
        "operator_summary": spec.get("summary", ""),
        "theme": spec.get("theme", existing.get("theme", "naval")),
        "density": spec.get("density", existing.get("density", "comfortable")),
        "generation_time_s": round(time.time() - t0, 1),
        "updated_at": time.time(),
        "widget_count": len(spec.get("widgets", [])),
        "status": "success",
        "errors": [],
        "has_backend": True,
        "has_frontend": True,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


async def iterate_app(slug: str, instruction: str) -> dict:
    existing = _load_manifest(slug)
    app_dir = _app_dir(slug)
    index_path = app_dir / "index.html"
    current_html = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    refined = await _refine_blueprint(existing, instruction)
    manifest = await update_app(slug, refined["name"], refined["description"], refined["context"])
    refreshed_html = index_path.read_text(encoding="utf-8") if index_path.exists() else current_html
    rewritten_html = await _rewrite_app_frontend(slug, instruction, refreshed_html or current_html)
    if rewritten_html:
        index_path.write_text(rewritten_html, encoding="utf-8")
    latest = _load_manifest(slug)
    history = list(latest.get("iteration_history", []))
    history.append({
        "role": "user",
        "content": _clean_copy(instruction),
        "ts": time.time(),
    })
    history.append({
        "role": "assistant",
        "content": refined["reply"],
        "ts": time.time(),
    })
    latest["iteration_history"] = history[-24:]
    _save_manifest(slug, latest)
    return {
        **latest,
        "reply": refined["reply"],
    }


def list_generated_apps() -> list[dict]:
    apps = []
    for manifest_file in sorted(GENERATED_APPS_DIR.glob("*/manifest.json")):
        try:
            apps.append(json.loads(manifest_file.read_text()))
        except Exception:
            pass
    apps.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return apps


def get_app_frontend(slug: str) -> Optional[str]:
    get_app_spec(slug)
    html_file = GENERATED_APPS_DIR / slug / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return None


def delete_app(slug: str) -> bool:
    import shutil
    app_dir = GENERATED_APPS_DIR / slug
    if app_dir.exists():
        shutil.rmtree(app_dir)
        return True
    return False
