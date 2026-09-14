"""Bounded, allowlisted audit of input in the manual verification window."""
import math
import os
import time
from datetime import datetime, timezone
from uuid import uuid4


MAX_EVENTS = min(30000, max(1, int(os.getenv("ECHEMI_MANUAL_RECORDING_MAX_EVENTS", "20000"))))
MAX_SESSIONS = min(30, max(1, int(os.getenv("ECHEMI_MANUAL_RECORDING_MAX_SESSIONS", "10"))))


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def client_time(message):
    value = message.get("t_ms")
    if value is None:  # Old clients remain compatible.
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 3600000:
        raise ValueError("Invalid pointer time")
    return round(value, 3)


class RecordingBudget:
    def __init__(self, max_events=MAX_EVENTS, max_sessions=MAX_SESSIONS):
        self.max_events, self.max_sessions = max_events, max_sessions
        self.events = self.sessions = 0

    def start(self, manual_event):
        if self.sessions >= self.max_sessions:
            manual_event["recordings_omitted"] = manual_event.get("recordings_omitted", 0) + 1
            return None
        self.sessions += 1
        recording = PointerRecording(self)
        manual_event["recordings"].append(recording.data)
        return recording


class PointerRecording:
    def __init__(self, budget):
        self.budget = budget
        self.started = time.monotonic()
        self.data = {"id": str(uuid4()), "schema_version": 1, "started_at": utc_now(),
                     "ended_at": None, "stop_reason": None, "duration_ms": 0,
                     "viewport": {"width": 1280, "height": 900}, "events": [],
                     "dropped_events": 0, "limit_reached": False}

    def elapsed(self):
        return round((time.monotonic() - self.started) * 1000, 3)

    def append(self, kind, x, y, t_ms, received_at=None):
        if self.budget.events >= self.budget.max_events:
            self.data["limit_reached"] = True
            self.data["dropped_events"] += 1
            return None
        event = {"type": kind, "x": round(x, 3), "y": round(y, 3), "client_t_ms": t_ms,
                 "received_t_ms": round(((received_at if received_at is not None else time.monotonic()) - self.started) * 1000, 3),
                 "delivered_t_ms": None, "delivery": "pending"}
        self.data["events"].append(event)
        self.budget.events += 1
        return event

    def delivered(self, event):
        if event is not None:
            event.update(delivered_t_ms=self.elapsed(), delivery="delivered")

    def finish(self, reason):
        if self.data["ended_at"] is not None:
            return
        for event in self.data["events"]:
            if event["delivery"] == "pending":
                event["delivery"] = "interrupted"
        self.data.update(ended_at=utc_now(), duration_ms=self.elapsed(), stop_reason=reason)

    def state(self):
        return {"type": "recording", "id": self.data["id"], "event_count": len(self.data["events"]),
                "limited": self.data["limit_reached"], "dropped_events": self.data["dropped_events"]}
