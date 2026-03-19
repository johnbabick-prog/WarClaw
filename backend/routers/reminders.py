"""Reminder management endpoints."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.reminders import create_reminder, delete_reminder, list_reminders

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


class ReminderRequest(BaseModel):
    title: str
    message: str = ""
    cadence: str
    schedule: dict


@router.get("/")
def get_reminders():
    return {"reminders": list_reminders()}


@router.post("/")
def add_reminder(req: ReminderRequest):
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Reminder title is required")
    reminder = create_reminder(req.title, req.message, req.cadence, req.schedule)
    return {"status": "created", "reminder": reminder}


@router.delete("/{reminder_id}")
def remove_reminder(reminder_id: str):
    if not delete_reminder(reminder_id):
        raise HTTPException(status_code=404, detail="Reminder not found")
    return {"status": "deleted", "id": reminder_id}
