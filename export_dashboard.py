from __future__ import annotations

import json
import math
import shutil
import os
import stat
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "processed_cache" / "latest_processed_analysis.xlsx"
PUBLIC = ROOT / "frontend"
OUT_JSON = PUBLIC / "dashboard_data.json"
OUT_XLSX = PUBLIC / "latest_processed_analysis.xlsx"

SESSION_COLS = [
    "week", "student_id", "conversation_id", "class",
    "completion_code_requested", "first_code_request_after_questions",
    "completion_code_received", "work_count_for_threshold",
    "code_request_count", "answer_attempts", "non_completion_reason",
]
TURN_COLS = [
    "week", "student_id", "conversation_id", "class",
    "is_answer_attempt", "seriousness", "student_answer", "answer_result",
    "followups_to_recovery", "question_type", "is_followup", "is_follow_up",
    "followup_question", "is_followup_question", "assistant_question",
]
REQUEST_COLS = [
    "week", "student_id", "conversation_id", "class", "request_number",
    "questions_before_request", "answer_attempts_before_request", "request_text",
    "observed_request_outcome",
]
VALIDATION_COLS = ["check", "status", "severity", "detail"]



def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    try:
        path.chmod(path.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(path)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot update {path}. Close the file if it is open and make sure the project folder is writable."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)


def _atomic_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(dst)
    fd, tmp_name = tempfile.mkstemp(prefix=dst.name + ".", suffix=".tmp", dir=str(dst.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot update {dst}. Close the file if it is open and make sure the project folder is writable."
        ) from exc
    finally:
        tmp.unlink(missing_ok=True)

def _records(df: pd.DataFrame, wanted):
    if df is None or df.empty:
        return []
    cols = [c for c in wanted if c in df.columns]
    out = df[cols].copy()
    out = out.replace({np.nan: None, pd.NA: None})

    def clean(v):
        if v is None:
            return None
        if isinstance(v, (np.bool_,)):
            return bool(v)
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            f = float(v)
            return None if math.isnan(f) or math.isinf(f) else f
        if isinstance(v, pd.Timestamp):
            return v.isoformat()
        return v

    return [{k: clean(v) for k, v in row.items()} for row in out.to_dict("records")]


def main():
    if not WORKBOOK.exists():
        raise SystemExit(
            f"Missing {WORKBOOK}. Run the local Streamlit processor first so the cumulative workbook exists."
        )

    xls = pd.ExcelFile(WORKBOOK)
    sessions = pd.read_excel(xls, "Session_Summary")
    turns = pd.read_excel(xls, "Turn_Level_Analysis")
    requests = pd.read_excel(xls, "Code_Request_Events")
    validation = pd.read_excel(xls, "Validation_Report") if "Validation_Report" in xls.sheet_names else pd.DataFrame()

    weeks = sorted(pd.to_numeric(sessions.get("week"), errors="coerce").dropna().astype(int).unique().tolist())
    classes = sorted(sessions.get("class", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())

    payload = {
        "meta": {
            "schema_version": 1,
            "weeks": weeks,
            "classes": classes,
            "session_count": int(len(sessions)),
            "turn_count": int(len(turns)),
            "request_event_count": int(len(requests)),
        },
        "sessions": _records(sessions, SESSION_COLS),
        "turns": _records(turns, TURN_COLS),
        "requests": _records(requests, REQUEST_COLS),
        "validation": _records(validation, VALIDATION_COLS),
    }

    PUBLIC.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(OUT_JSON, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    _atomic_copy(WORKBOOK, OUT_XLSX)
    print(f"Wrote {OUT_JSON} ({OUT_JSON.stat().st_size / 1024:.1f} KB)")
    print(f"Copied {OUT_XLSX}")


if __name__ == "__main__":
    main()
