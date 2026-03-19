"""Deterministic assistant actions for common WarClaw operator requests."""
import re
from datetime import datetime

from .app_factory import add_app_task, generate_app, list_generated_apps
from .reports import generate_report
from .lan_monitor import scan_network
from .reminders import create_reminder, list_reminders


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _looks_like_scan_request(text: str) -> bool:
    text = text.lower()
    return any(phrase in text for phrase in (
        "scan the lan",
        "scan lan",
        "scan the network",
        "scan network",
        "discover systems",
        "discover the lan",
    ))


def _looks_like_report_request(text: str) -> bool:
    text = text.lower()
    return "report" in text or "opord summary" in text or "watch brief" in text or "briefing" in text


def _looks_like_app_request(text: str) -> bool:
    text = text.lower()
    return any(word in text for word in ("create", "build", "generate")) and any(
        word in text for word in ("app", "dashboard", "generator", "workspace", "tool")
    )


def _looks_like_reminder_request(text: str) -> bool:
    text = text.lower()
    return "remind me" in text or "set a reminder" in text or "recurring reminder" in text


def _looks_like_todo_request(text: str) -> bool:
    text = text.lower()
    return any(phrase in text for phrase in (
        "todo list", "to do list", "task list", "checklist", "daily list", "carry forward",
        "add to my todo", "add to my task list", "create my todo",
    ))


def _find_task_workspace() -> dict | None:
    apps = list_generated_apps()
    return next((app for app in apps if app.get("app_kind") == "task_workspace"), None)


async def _ensure_task_workspace() -> dict:
    existing = _find_task_workspace()
    if existing:
        return existing
    return await generate_app(
        app_name="Daily Task Board",
        description="Create a daily todo list based on unfinished actions and track completion.",
        context="Requested directly from the WarClaw AI Assistant.",
    )


def _split_task_items(text: str) -> list[str]:
    body = _normalize(text)
    has_explicit_list = ":" in body or any(sep in body for sep in (";", ",", "\n"))
    if ":" in body:
        body = body.split(":", 1)[1]
    body = re.sub(r"^(create|make|build|add)( me)?( a)?\s+(todo list|to do list|task list|checklist)\s*(for|based on)?", "", body, flags=re.IGNORECASE).strip()
    if not has_explicit_list and any(phrase in body.lower() for phrase in ("did not complete yesterday", "unfinished actions", "based on the actions", "from yesterday")):
        return []
    parts = re.split(r"(?:\n|;|,|\band\b)", body)
    cleaned = []
    for part in parts:
        item = re.sub(r"^\s*[-*]\s*", "", part).strip(" .")
        if len(item) >= 4:
            cleaned.append(item[0].upper() + item[1:])
    deduped = []
    for item in cleaned:
        if item.lower() not in {d.lower() for d in deduped}:
            deduped.append(item)
    return deduped[:12]


def _parse_reminder(text: str) -> dict | None:
    lowered = text.lower()
    match = re.search(r"(?:remind me|set (?:a )?recurring reminder(?: for me)?)(?: every)?\s+(day|daily|hourly|monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?: at (\d{1,2}(?::\d{2})?(?:am|pm)?|\d{4}))?\s+to\s+(.+)", lowered, flags=re.IGNORECASE)
    if not match:
        return None
    cadence_raw, time_raw, action = match.groups()
    cadence_raw = cadence_raw.lower()
    weekday_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
    }
    schedule = {}
    title_action = _normalize(action)

    if cadence_raw in {"day", "daily"}:
        cadence = "daily"
    elif cadence_raw == "hourly":
        cadence = "hourly"
        schedule["interval_s"] = 3600
    else:
        cadence = "weekly"
        schedule["weekday"] = weekday_map[cadence_raw]

    if cadence != "hourly":
        hour = 8
        minute = 0
        if time_raw:
            raw = time_raw.strip().lower()
            if re.fullmatch(r"\d{4}", raw):
                hour = int(raw[:2])
                minute = int(raw[2:])
            else:
                dt = datetime.strptime(raw, "%I%p" if ":" not in raw and raw.endswith(("am", "pm")) else "%I:%M%p")
                hour = dt.hour
                minute = dt.minute
        schedule["hour"] = hour
        schedule["minute"] = minute

    return {
        "title": f"Reminder: {title_action[:72]}",
        "message": title_action,
        "cadence": cadence,
        "schedule": schedule,
    }


def _format_reminder(reminder: dict) -> str:
    cadence = reminder.get("cadence", "scheduled")
    schedule = reminder.get("schedule") or {}
    if cadence == "daily":
        when = f"daily at {int(schedule.get('hour', 8)):02d}:{int(schedule.get('minute', 0)):02d}"
    elif cadence == "weekly":
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        when = f"every {weekdays[int(schedule.get('weekday', 0))]} at {int(schedule.get('hour', 8)):02d}:{int(schedule.get('minute', 0)):02d}"
    elif cadence == "hourly":
        when = "hourly"
    else:
        when = cadence
    return f"- `{reminder['id'][:8]}` {reminder['message']} ({when})"


def _titleize_request(text: str) -> str:
    cleaned = _normalize(text)
    cleaned = re.sub(r"^(create|build|generate)( me)?( a| an)?\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(app|dashboard|generator|workspace|tool)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return "Generated App"
    words = []
    for word in cleaned.split():
        if word.upper() in {"OPORD", "LAN", "NMEA", "MODBUS", "AI"}:
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    title = " ".join(words)
    if "Generator" not in title and "Dashboard" not in title and "Workspace" not in title:
        title += " Generator"
    return title


async def try_handle_action(message: str) -> str | None:
    text = _normalize(message)
    lowered = text.lower()

    if _looks_like_scan_request(text):
        result = await scan_network()
        try:
            from ..routers import lan as lan_router  # local import to avoid cycles at module load
            lan_router._last_scan_result = {
                "network": result.network,
                "hosts_scanned": result.hosts_scanned,
                "hosts_up": result.hosts_up,
                "scan_duration_s": result.scan_duration_s,
                "recommendations": result.recommendations,
                "hosts": [
                    {
                        "ip": h.ip,
                        "hostname": h.hostname,
                        "open_ports": h.open_ports,
                        "services": [
                            {
                                "port": s.port,
                                "protocol": s.protocol,
                                "banner": s.banner,
                                "latency_ms": s.latency_ms,
                            }
                            for s in h.services
                        ],
                        "integration_hints": h.integration_hints,
                    }
                    for h in result.discovered
                ],
            }
        except Exception:
            pass
        recs = "\n".join(f"- {item}" for item in result.recommendations[:4]) or "- No immediate recommendations."
        return (
            f"LAN scan completed.\n\n"
            f"- Network: `{result.network}`\n"
            f"- Hosts up: `{result.hosts_up}`\n"
            f"- Duration: `{result.scan_duration_s}s`\n\n"
            f"Recommended next actions:\n{recs}"
        )

    if "list reminders" in lowered or "show reminders" in lowered:
        reminders = list_reminders()
        lines = "\n".join(_format_reminder(reminder) for reminder in reminders[:10]) or "- No reminders configured."
        return f"Current reminders:\n{lines}"

    if _looks_like_reminder_request(text):
        parsed = _parse_reminder(text)
        if parsed is None:
            return (
                "I can create recurring reminders, but I need a schedule in the request. "
                "Example: `Remind me every day at 0600 to review GPS integrity` or "
                "`Set a recurring reminder every Monday at 08:30am to publish the watch brief`."
            )
        reminder = create_reminder(parsed["title"], parsed["message"], parsed["cadence"], parsed["schedule"], source="assistant")
        return (
            f"Action completed: created reminder **{parsed['message']}**.\n\n"
            f"{_format_reminder(reminder)}\n"
            f"WarClaw will emit this reminder into the mission log when it becomes due."
        )

    if _looks_like_todo_request(text):
        items = _split_task_items(text)
        if not items:
            return (
                "I can build or update a todo list or checklist, but I need you to provide the actual actions to add. "
                "Paste them after a colon or as a comma-separated list, for example: "
                "`Create a todo list: validate GPS feed, brief engineering, review LAN scan`."
            )
        workspace = await _ensure_task_workspace()
        created = [add_app_task(workspace["slug"], item, due_label="Next watch", source="assistant") for item in items]
        return (
            f"Action completed: added {len(created)} task(s) to **{workspace['name']}**.\n\n"
            + "\n".join(f"- {task['title']}" for task in created[:8])
            + f"\n\nOpen: [Daily task board](/api/apps/{workspace['slug']}/ui)"
        )

    if _looks_like_app_request(text):
        app_name = _titleize_request(text)
        manifest = await generate_app(
            app_name=app_name,
            description=text,
            context="Requested directly from the WarClaw AI Assistant.",
        )
        return (
            f"Action completed: created app **{manifest['name']}**.\n\n"
            f"- Slug: `{manifest['slug']}`\n"
            f"- Status: `{manifest['status']}`\n"
            f"- Open: [Launch app](/api/apps/{manifest['slug']}/ui)"
        )

    if _looks_like_report_request(text):
        report_type = "daily_opord_summary" if "opord" in lowered or "daily" in lowered else "network_posture"
        result = await generate_report(report_type, text)
        report = result["report"]
        actions = "\n".join(f"- {item}" for item in report.get("recommended_actions", [])[:4])
        return (
            f"Action completed: generated report **{report['title']}**.\n\n"
            f"{report['summary']}\n\n"
            f"Recommended actions:\n{actions}"
        )

    return None
