from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

import pandas as pd

from processor import process_uploaded_files as _base_process_uploaded_files
from processor import build_validation_report

ADELAIDE = ZoneInfo("Australia/Adelaide")

CLASS_ORDER = [
    "Wednesday 08:00–10:00",
    "Wednesday 10:00–12:00",
    "Thursday 12:00–14:00",
    "Friday 12:00–14:00",
    "Outside scheduled classes",
]


def _class_from_adelaide_timestamp(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "Outside scheduled classes"
    try:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        ts = ts.tz_convert(ADELAIDE)
    except Exception:
        return "Outside scheduled classes"

    weekday = ts.day_name()
    t = ts.time().replace(tzinfo=None)
    if weekday == "Wednesday" and time(8, 0) <= t < time(10, 0):
        return "Wednesday 08:00–10:00"
    if weekday == "Wednesday" and time(10, 0) <= t < time(12, 0):
        return "Wednesday 10:00–12:00"
    if weekday == "Thursday" and time(12, 0) <= t < time(14, 0):
        return "Thursday 12:00–14:00"
    if weekday == "Friday" and time(12, 0) <= t < time(14, 0):
        return "Friday 12:00–14:00"
    return "Outside scheduled classes"


def _normalise_raw_keys(raw: pd.DataFrame) -> pd.DataFrame:
    out = raw.copy()
    if "Conversation Id" in out.columns and "conversation_id" not in out.columns:
        out["conversation_id"] = out["Conversation Id"]
    if "User Id" in out.columns and "student_id" not in out.columns:
        out["student_id"] = out["User Id"]
    return out


def process_uploaded_files(files):
    raw, turns, sessions, questions, requests = _base_process_uploaded_files(files)
    raw = _normalise_raw_keys(raw)

    if raw.empty:
        return raw, turns, sessions, questions, requests

    ts_col = "Timestamp" if "Timestamp" in raw.columns else None
    if ts_col:
        raw["class"] = raw[ts_col].map(_class_from_adelaide_timestamp)
    else:
        raw["class"] = "Outside scheduled classes"

    session_class = (
        raw.sort_values([c for c in ["week", "conversation_id", ts_col] if c])
        .groupby(["week", "conversation_id"], as_index=False)
        .first()[["week", "conversation_id", "class"]]
    )

    def attach(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df.copy() if df is not None else pd.DataFrame()
        if not {"week", "conversation_id"}.issubset(df.columns):
            return df.copy()
        out = df.drop(columns=["class"], errors="ignore").merge(
            session_class,
            on=["week", "conversation_id"],
            how="left",
        )
        out["class"] = out["class"].fillna("Outside scheduled classes")
        return out

    turns = attach(turns)
    sessions = attach(sessions)
    requests = attach(requests)

    return raw, turns, sessions, questions, requests
