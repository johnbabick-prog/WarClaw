"""Operational report generation for WarClaw."""
import json
import time

from .agent_engine import agent_engine
from .app_factory import list_generated_apps
from .chat_sessions import list_sessions
from .llm import llm_service
from .mission_log import recent, emit


def _build_report_context(limit: int = 200) -> dict:
    events = recent(limit)
    agent_summary = agent_engine.summary
    apps = list_generated_apps()
    sessions = list_sessions()
    return {
        "generated_at": time.time(),
        "events": events,
        "agents": agent_summary,
        "apps": apps,
        "chat_sessions": sessions,
        "event_counts": {
            "alerts": sum(1 for evt in events if evt.get("level") == "alert"),
            "warnings": sum(1 for evt in events if evt.get("level") == "warn"),
            "lan_events": sum(1 for evt in events if evt.get("category") == "lan"),
            "app_events": sum(1 for evt in events if evt.get("category") == "app"),
        },
    }


async def generate_report(report_type: str, focus: str = "") -> dict:
    context = _build_report_context()
    prompt = f"""Create a concise operational report for WarClaw.

Report type: {report_type}
Focus: {focus or "General ship operations"}

Return JSON only with this schema:
{{
  "title": "string",
  "summary": "string",
  "sections": [
    {{"heading": "string", "body": "string"}}
  ],
  "recommended_actions": ["string"]
}}

Operational context:
{json.dumps(context, indent=2)}
"""

    report = None
    if llm_service.ready:
        response = "".join([
            token async for token in llm_service.astream_chat(
                [],
                prompt,
                max_tokens=1600,
                temperature=0.2,
            )
        ]).strip()
        try:
            report = json.loads(response)
        except Exception:
            report = None

    if report is None:
        counts = context["event_counts"]
        report = {
            "title": f"{report_type.title()} Report",
            "summary": (
                f"{len(context['events'])} mission events reviewed, "
                f"{counts['alerts']} alerts, {counts['warnings']} warnings, "
                f"{len(context['agents'])} agents deployed, {len(context['apps'])} generated apps."
            ),
            "sections": [
                {
                    "heading": "Operational Overview",
                    "body": "WarClaw recorded shipboard system activity from mission events, agent activity, and generated application usage.",
                },
                {
                    "heading": "Network And Systems",
                    "body": f"LAN-related events: {counts['lan_events']}. Review the mission log for device discovery and protocol observations.",
                },
                {
                    "heading": "Automation Posture",
                    "body": f"Agents deployed: {len(context['agents'])}. Generated apps available: {len(context['apps'])}. Chat sessions recorded: {len(context['chat_sessions'])}.",
                },
            ],
            "recommended_actions": [
                "Review alert-level mission log entries.",
                "Confirm critical agents are running before next watch rotation.",
                "Generate or refresh dashboards for recently discovered systems.",
            ],
        }

    emit("success", "system", f"Generated report: {report.get('title', report_type)}", {
        "report_type": report_type,
        "focus": focus,
    })
    return {
        "report_type": report_type,
        "focus": focus,
        "generated_at": time.time(),
        "context": context["event_counts"],
        "report": report,
    }

