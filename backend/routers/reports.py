"""Operational report endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel

from ..services.reports import generate_report

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportRequest(BaseModel):
    report_type: str = "daily_opord_summary"
    focus: str = ""


@router.get("/templates")
def list_report_templates():
    return {
        "templates": [
            {
                "id": "daily_opord_summary",
                "name": "Daily OPORD Summary",
                "description": "Summarize mission events, system posture, alerts, and recommended next actions.",
            },
            {
                "id": "engineering_watch",
                "name": "Engineering Watch Brief",
                "description": "Highlight engineering-related observations, anomalies, and monitor posture.",
            },
            {
                "id": "network_posture",
                "name": "Network Posture Report",
                "description": "Summarize LAN activity, protocol visibility, and operational risk signals.",
            },
        ]
    }


@router.post("/generate")
async def create_report(req: ReportRequest):
    return await generate_report(req.report_type, req.focus)
