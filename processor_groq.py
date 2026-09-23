
"""
processor_groq.py

Checkpoint-safe Gemini enrichment wrapper for the existing processor.py.

This file:
- DOES NOT replace processor.py
- calls the existing deterministic processor first
- uses Gemini only for semantic/ambiguous fields
- saves every successful Gemini response immediately to llm_cache.json
- writes llm_state.json continuously so progress is visible
- stops gracefully on rate-limit / API failure instead of losing progress
- resumes from llm_cache.json on the next run
- keeps the same process_uploaded_files(...) return signature expected by app.py
"""

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import unquote

import pandas as pd

from processor_with_classes import process_uploaded_files as _process_uploaded_files_base
from processor_with_classes import build_validation_report
from llm_provider import get_llm_provider

CACHE_PATH = Path(__file__).with_name("llm_cache.json")
STATE_PATH = Path(__file__).with_name("llm_state.json")
LLM_COMPLETION_ACCEPT_THRESHOLD = 0.85
TURN_PROMPT_VERSION = "turn_dashboard_v3"

PROCESSED_CACHE_DIR = Path(__file__).with_name("processed_cache")
PROCESSED_CACHE_VERSION = "processed_excel_v2"

# Prevent overlapping Streamlit reruns/threads from writing the JSON files at the same time.
_JSON_WRITE_LOCK = threading.RLock()



def _uploaded_file_bytes(file_obj) -> bytes:
    if hasattr(file_obj, "getvalue"):
        return file_obj.getvalue()

    if isinstance(file_obj, (str, os.PathLike, Path)):
        return Path(file_obj).read_bytes()

    pos = None
    try:
        pos = file_obj.tell()
    except Exception:
        pass

    try:
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        data = file_obj.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        return data
    finally:
        if pos is not None and hasattr(file_obj, "seek"):
            try:
                file_obj.seek(pos)
            except Exception:
                pass


def _processed_signature(files, use_llm: bool = True) -> str:
    h = hashlib.sha256()
    h.update(PROCESSED_CACHE_VERSION.encode("utf-8"))
    h.update(TURN_PROMPT_VERSION.encode("utf-8"))
    h.update(str(bool(use_llm)).encode("utf-8"))
    h.update(os.getenv("GEMINI_MODEL", "").encode("utf-8"))

    for f in files:
        name = unquote(str(getattr(f, "name", None) or str(f)))
        h.update(b"\\nFILE\\n")
        h.update(str(name).encode("utf-8", errors="ignore"))
        h.update(b"\\n")
        h.update(_uploaded_file_bytes(f))

    return h.hexdigest()


def _processed_workbook_path(signature: str) -> Path:
    PROCESSED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return PROCESSED_CACHE_DIR / f"processed_{signature}.xlsx"



def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return an Excel-safe copy of a DataFrame.

    Excel cannot store timezone-aware datetime values. This removes timezone
    information while preserving the displayed local date/time value.
    """
    out = df.copy()

    for col in out.columns:
        series = out[col]

        if pd.api.types.is_datetime64_any_dtype(series):
            try:
                if getattr(series.dt, "tz", None) is not None:
                    out[col] = series.dt.tz_localize(None)
            except Exception:
                pass

        elif series.dtype == "object":
            def _clean_value(value):
                try:
                    if isinstance(value, pd.Timestamp):
                        return value.tz_localize(None) if value.tz is not None else value

                    import datetime as _dt
                    if isinstance(value, _dt.datetime) and value.tzinfo is not None:
                        return value.replace(tzinfo=None)
                except Exception:
                    pass
                return value

            out[col] = series.map(_clean_value)

    return out


def _save_processed_workbook(
    signature: str,
    raw: pd.DataFrame,
    turns: pd.DataFrame,
    sessions: pd.DataFrame,
    questions: pd.DataFrame,
    requests: pd.DataFrame,
) -> Path:
    path = _processed_workbook_path(signature)
    tmp = path.with_name(path.stem + ".tmp.xlsx")

    with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        _excel_safe(raw).to_excel(writer, sheet_name="Raw_Data", index=False)
        _excel_safe(turns).to_excel(writer, sheet_name="Turn_Analysis", index=False)
        _excel_safe(sessions).to_excel(writer, sheet_name="Session_Summary", index=False)
        _excel_safe(questions).to_excel(writer, sheet_name="Question_Summary", index=False)
        _excel_safe(requests).to_excel(writer, sheet_name="Code_Request_Events", index=False)

    os.replace(tmp, path)
    return path


def _load_processed_workbook(signature: str):
    path = _processed_workbook_path(signature)
    if not path.exists():
        return None

    try:
        book = pd.ExcelFile(path)
        required = {
            "Raw_Data",
            "Turn_Analysis",
            "Session_Summary",
            "Question_Summary",
            "Code_Request_Events",
        }
        if not required.issubset(set(book.sheet_names)):
            return None

        return (
            pd.read_excel(book, sheet_name="Raw_Data"),
            pd.read_excel(book, sheet_name="Turn_Analysis"),
            pd.read_excel(book, sheet_name="Session_Summary"),
            pd.read_excel(book, sheet_name="Question_Summary"),
            pd.read_excel(book, sheet_name="Code_Request_Events"),
        )
    except Exception:
        return None


def get_saved_processed_workbook(files, use_llm: bool = True):
    signature = _processed_signature(list(files), use_llm=use_llm)
    path = _processed_workbook_path(signature)
    return path if path.exists() else None


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()




def _first_feedback_line(value: Any) -> str:
    """
    Return only the first meaningful feedback cue for semantic classification.

    Assistant messages often contain both feedback on the student's answer and the
    next assessment question. Passing the whole message can make the LLM judge the
    student's answer against that next question. Keep the full message in the turn
    dataset, but send only the opening feedback cue to the LLM.
    """
    text = _safe_text(value)
    if not text:
        return ""

    # Stop at an explicit next-question section, whether it starts on a new line
    # or appears later in the same assistant message.
    import re
    text = re.split(r"(?:\r?\n|\s{2,})Question\s*[:\n]?", text, maxsplit=1, flags=re.I)[0].strip()

    # Prefer the first non-empty line.
    for line in text.splitlines():
        line = line.strip()
        if line:
            text = line
            break

    # If the line still contains a clear transition to a new Question, cut there.
    text = re.split(r"\s+Question\s+(?=[A-Z0-9])", text, maxsplit=1)[0].strip()

    # Keep it brief. One sentence normally carries the strongest evaluation cue.
    m = re.match(r"^(.+?[.!?])(?:\s|$)", text)
    if m:
        text = m.group(1).strip()

    return text[:500]

def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _atomic_write_json(
    path: Path,
    data: Dict[str, Any],
    retries: int = 12,
    retry_delay: float = 0.15,
) -> None:
    """
    Windows-safe atomic JSON write.

    Streamlit may rerun in overlapping threads, and Windows may briefly lock
    a destination or temp file. This implementation serializes writes,
    uses a unique temp file for every write, and retries os.replace().
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)

    with _JSON_WRITE_LOCK:
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f"{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_name = tmp.name

            last_error = None
            for attempt in range(retries):
                try:
                    os.replace(tmp_name, path)
                    tmp_name = None
                    return
                except PermissionError as exc:
                    last_error = exc
                    time.sleep(retry_delay * (attempt + 1))

            raise last_error

        finally:
            if tmp_name:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except Exception:
                    pass


def _load_cache() -> Dict[str, Any]:
    return _load_json(CACHE_PATH)


def _save_cache(cache: Dict[str, Any]) -> None:
    _atomic_write_json(CACHE_PATH, cache)


def _save_state(state: Dict[str, Any]) -> None:
    # State is only progress metadata. A brief Windows lock should never
    # terminate the expensive LLM run; the cache remains the source of truth.
    try:
        _atomic_write_json(STATE_PATH, state)
    except PermissionError:
        pass


def _hash_key(prefix: str, *parts: str) -> str:
    payload = "\n---\n".join([prefix] + [_safe_text(x) for x in parts])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    status = getattr(exc, "status_code", None)
    return (
        status == 429
        or "429" in text
        or "rate limit" in text
        or "too many requests" in text
        or "requests per day" in text
        or "requests per minute" in text
        or "tokens per day" in text
        or "tokens per minute" in text
    )


def _normalize_turn_result(result: Dict[str, Any]) -> Dict[str, Any]:
    confidence = result.get("confidence") or {}

    seriousness_raw = str(result.get("seriousness", "")).upper().strip()
    seriousness = (
        "Serious" if seriousness_raw == "SERIOUS"
        else "Non-serious" if seriousness_raw == "NON_SERIOUS"
        else None
    )

    answer_map = {
        "CORRECT": "Correct",
        "PARTIALLY_CORRECT": "Partially correct",
        "INCORRECT": "Incorrect",
        "UNCLEAR": "Unclear",
    }
    answer_result = answer_map.get(str(result.get("answer_result", "")).upper().strip())

    qtype_map = {
        "CODING": "Coding",
        "CONCEPTUAL": "Conceptual",
    }
    question_type = qtype_map.get(str(result.get("question_type", "")).upper().strip())

    followup = result.get("is_followup_question")
    if not isinstance(followup, bool):
        followup = None

    return {
        "seriousness": seriousness,
        "answer_result": answer_result,
        "question_type": question_type,
        "is_followup_question": followup,
        "reason": _safe_text(result.get("reason")),
        "confidence": confidence,
        "model": (result.get("_meta") or {}).get("model"),
    }

def _semantic_enrich_turns(
    turns: pd.DataFrame,
    provider,
    cache: Dict[str, Any],
) -> Tuple[pd.DataFrame, bool]:
    """
    Classify every observed academic answer attempt with the focused dashboard schema.

    Strong deterministic evidence is retained:
    - obvious gibberish can remain Non-serious;
    - explicit chatbot correctness feedback remains authoritative;
    - strong direct code evidence can remain Coding.

    All other semantic judgements come from the LLM. This prevents weak deterministic
    heuristics from locking in false certainty while preserving objective evidence.
    """
    if turns.empty:
        return turns, False

    turns = turns.copy()
    for col in [
        "llm_model", "llm_prompt_version", "llm_error", "llm_reason",
        "seriousness_confidence", "answer_result_confidence",
        "question_type_confidence", "followup_confidence",
        "is_followup_question",
    ]:
        if col not in turns.columns:
            turns[col] = None

    candidates = [
        idx for idx, row in turns.iterrows()
        if bool(row.get("is_answer_attempt", False))
    ]

    # Build previous-answer-turn context within each session for Q3a follow-up detection.
    previous_by_idx = {}
    answer_view = turns.loc[candidates].copy()
    if len(answer_view):
        answer_view["_ts"] = pd.to_datetime(answer_view.get("timestamp"), errors="coerce")
        sort_cols = [c for c in ["week", "conversation_id", "_ts"] if c in answer_view.columns]
        if sort_cols:
            answer_view = answer_view.sort_values(sort_cols, kind="stable")
        for _, group in answer_view.groupby([c for c in ["week", "conversation_id"] if c in answer_view.columns], sort=False):
            prev_idx = None
            for idx in group.index:
                previous_by_idx[idx] = prev_idx
                prev_idx = idx

    total_candidates = len(candidates)
    completed_candidates = 0
    _save_state({
        "status": "running", "stage": "turn_classification",
        "completed_turn_candidates": 0, "total_turn_candidates": total_candidates,
        "cache_entries": len(cache), "prompt_version": TURN_PROMPT_VERSION,
    })

    for idx in candidates:
        row = turns.loc[idx]
        question = _safe_text(row.get("assistant_question"))
        answer = _safe_text(row.get("student_answer"))
        feedback = _first_feedback_line(row.get("assistant_feedback"))

        prev_idx = previous_by_idx.get(idx)
        if prev_idx is not None:
            prev = turns.loc[prev_idx]
            previous_question = _safe_text(prev.get("assistant_question"))
            previous_answer = _safe_text(prev.get("student_answer"))
            # The previous assistant message often already contains the CURRENT
            # question, so do not duplicate it as previous-feedback context.
            previous_feedback = ""
        else:
            previous_question = previous_answer = previous_feedback = ""

        key = _hash_key(
            TURN_PROMPT_VERSION,
            previous_question, previous_answer, previous_feedback,
            question, answer, feedback,
        )

        if key in cache:
            result = cache[key]
        else:
            if provider is None:
                raise RuntimeError(
                    "LLM cache miss while GEMINI_API_KEY is unavailable. "
                    "At least one answer turn still needs Gemini classification. "
                    "Restore the API key or process this dataset once with Gemini so the result is cached."
                )
            try:
                result = provider.classify_turn(
                    question=question,
                    answer=answer,
                    feedback=feedback,
                    previous_question=previous_question,
                    previous_answer=previous_answer,
                    previous_feedback=previous_feedback,
                )
                cache[key] = result
                _save_cache(cache)
            except Exception as exc:
                turns.at[idx, "llm_error"] = str(exc)
                if _is_rate_limit_error(exc):
                    _save_state({
                        "status": "stopped", "stage": "turn_classification",
                        "reason": "rate_limit", "error": str(exc),
                        "completed_turn_candidates": completed_candidates,
                        "total_turn_candidates": total_candidates,
                        "cache_entries": len(cache), "prompt_version": TURN_PROMPT_VERSION,
                    })
                    return turns, True
                completed_candidates += 1
                _save_state({
                    "status": "running", "stage": "turn_classification",
                    "completed_turn_candidates": completed_candidates,
                    "total_turn_candidates": total_candidates,
                    "cache_entries": len(cache), "last_error": str(exc),
                    "prompt_version": TURN_PROMPT_VERSION,
                })
                continue

        parsed = _normalize_turn_result(result)
        conf = parsed["confidence"] or {}

        # Seriousness: retain only obvious deterministic non-serious evidence;
        # otherwise use the deliberately generous LLM judgement.
        strong_nonserious = (
            row.get("seriousness") == "Non-serious"
            and str(row.get("seriousness_source", "")).startswith("provisional_rule")
        )
        if not strong_nonserious and parsed["seriousness"]:
            turns.at[idx, "seriousness"] = parsed["seriousness"]
            turns.at[idx, "seriousness_source"] = "gemini_llm"
            turns.at[idx, "seriousness_confidence"] = conf.get("seriousness")
            turns.at[idx, "seriousness_review_required"] = False

        # Correctness: explicit assistant evaluation is stronger than model inference.
        explicit_correctness = row.get("answer_result_source") == "deterministic_text_evidence"
        if explicit_correctness:
            turns.at[idx, "answer_result_confidence"] = 1.0
            turns.at[idx, "answer_result_review_required"] = False
        elif parsed["answer_result"]:
            turns.at[idx, "answer_result"] = parsed["answer_result"]
            turns.at[idx, "answer_result_source"] = "gemini_llm"
            turns.at[idx, "answer_result_confidence"] = conf.get("answer_result")
            turns.at[idx, "answer_result_review_required"] = parsed["answer_result"] == "Unclear"

        # Consistency safeguard: if the chatbot explicitly evaluated this answer
        # as Correct / Partially correct / Incorrect, it was an academic answer
        # attempt. Do not allow an LLM seriousness error to create combinations
        # such as "Correct + Non-serious".
        if row.get("answer_result_source") == "deterministic_text_evidence" and row.get("answer_result") in {
            "Correct", "Partially correct", "Incorrect"
        }:
            turns.at[idx, "seriousness"] = "Serious"
            turns.at[idx, "seriousness_source"] = "deterministic_consistency"
            turns.at[idx, "seriousness_confidence"] = 1.0
            turns.at[idx, "seriousness_review_required"] = False

        # Q4 is semantic: always use the LLM's Coding-vs-Conceptual judgement.
        if parsed["question_type"]:
            turns.at[idx, "question_type"] = parsed["question_type"]
            turns.at[idx, "question_type_source"] = "gemini_llm"
            turns.at[idx, "question_type_confidence"] = conf.get("question_type")
            turns.at[idx, "question_type_review_required"] = False

        if parsed["is_followup_question"] is not None:
            turns.at[idx, "is_followup_question"] = parsed["is_followup_question"]
            turns.at[idx, "followup_source"] = "gemini_llm"
            turns.at[idx, "followup_confidence"] = conf.get("is_followup_question")

        # Reasons are useful for exceptions/review, not for routine correct turns.
        exceptional = (
            turns.at[idx, "seriousness"] == "Non-serious"
            or turns.at[idx, "answer_result"] in {"Incorrect", "Partially correct", "Unclear"}
        )
        turns.at[idx, "llm_reason"] = parsed["reason"] if exceptional else ""
        turns.at[idx, "llm_model"] = parsed["model"]
        turns.at[idx, "llm_prompt_version"] = TURN_PROMPT_VERSION

        flags = []
        for col in [
            "code_request_review_required", "seriousness_review_required",
            "answer_result_review_required", "question_type_review_required",
        ]:
            if col in turns.columns:
                flags.append(bool(turns.at[idx, col]))
        if parsed["is_followup_question"] is None:
            flags.append(True)
        turns.at[idx, "review_required"] = any(flags)

        completed_candidates += 1
        _save_state({
            "status": "running", "stage": "turn_classification",
            "completed_turn_candidates": completed_candidates,
            "total_turn_candidates": total_candidates,
            "cache_entries": len(cache), "prompt_version": TURN_PROMPT_VERSION,
        })

    return turns, False

def _compact_completion_transcript(
    raw: pd.DataFrame,
    week,
    conversation_id,
    requests: pd.DataFrame,
    max_chars: int = 24000,
) -> str:
    """Build a compact transcript from the first code request through session end."""
    if raw is None or raw.empty:
        return ""

    g = raw.copy()
    if "week" in g.columns:
        g = g[g["week"] == week]
    if "Conversation Id" in g.columns:
        g = g[g["Conversation Id"] == conversation_id]
    elif "conversation_id" in g.columns:
        g = g[g["conversation_id"] == conversation_id]
    else:
        return ""

    if g.empty:
        return ""

    timestamp_col = "Timestamp" if "Timestamp" in g.columns else "timestamp"
    role_col = "Role" if "Role" in g.columns else "role"
    message_col = "Message" if "Message" in g.columns else "message"

    g = g.copy()
    g["_ts"] = pd.to_datetime(g[timestamp_col], errors="coerce")
    g = g.sort_values("_ts", kind="stable")

    # Start at the first detected request when available. This keeps the LLM call
    # focused on the evidence relevant to whether the code was actually delivered.
    if requests is not None and not requests.empty and "request_timestamp" in requests.columns:
        rg = requests.copy()
        if "week" in rg.columns:
            rg = rg[rg["week"] == week]
        if "conversation_id" in rg.columns:
            rg = rg[rg["conversation_id"] == conversation_id]
        if not rg.empty:
            first_request = pd.to_datetime(rg["request_timestamp"], errors="coerce").min()
            if pd.notna(first_request):
                # Include one message immediately before the request for context.
                before = g[g["_ts"] < first_request].tail(1)
                after = g[g["_ts"] >= first_request]
                g = pd.concat([before, after], ignore_index=True)

    lines = []
    for _, row in g.iterrows():
        role = _safe_text(row.get(role_col)).upper() or "UNKNOWN"
        msg = _safe_text(row.get(message_col))
        if not msg:
            continue
        ts = row.get("_ts")
        ts_text = "" if pd.isna(ts) else f"[{ts}] "
        lines.append(f"{ts_text}{role}: {msg}")

    transcript = "\n".join(lines)
    if len(transcript) > max_chars:
        # Completion evidence is normally near/after the request, so keep the tail.
        transcript = transcript[-max_chars:]
    return transcript


def _normalize_completion_audit(result: Dict[str, Any]) -> Dict[str, Any]:
    received = result.get("code_received")
    if not isinstance(received, bool):
        received = None

    code = result.get("completion_code")
    if code is not None:
        code = str(code).strip()
        if not code or code.lower() in {"null", "none", "n/a", "unknown"}:
            code = None

    try:
        confidence = float(result.get("confidence"))
    except Exception:
        confidence = None

    evidence = _safe_text(result.get("evidence"))
    model = (result.get("_meta") or {}).get("model")
    return {
        "received": received,
        "code": code,
        "confidence": confidence,
        "evidence": evidence,
        "model": model,
    }


def _audit_completion_delivery(
    raw: pd.DataFrame,
    sessions: pd.DataFrame,
    requests: pd.DataFrame,
    provider,
    cache: Dict[str, Any],
) -> Tuple[pd.DataFrame, pd.DataFrame, bool]:
    """
    Independently validate completion-code delivery for every session that asked.

    Deterministic evidence remains the baseline. High-confidence LLM evidence can
    recover a deterministic false negative only when the LLM also extracts a
    literal code. Conflicts never silently overwrite a deterministic positive;
    they are retained and flagged for review.
    """
    if sessions is None or sessions.empty:
        return sessions, requests, False

    sessions = sessions.copy()
    requests = requests.copy() if requests is not None else pd.DataFrame()

    audit_cols = {
        "completion_code_received_deterministic": None,
        "completion_code_deterministic": None,
        "completion_code_received_llm": None,
        "completion_code_llm": None,
        "completion_validation_confidence": None,
        "completion_validation_evidence": None,
        "completion_validation_status": None,
        "completion_evidence_source": None,
        "completion_validation_model": None,
    }
    for col, default in audit_cols.items():
        if col not in sessions.columns:
            sessions[col] = default

    candidates = sessions.index[
        sessions.get("completion_code_requested", pd.Series(False, index=sessions.index)).fillna(False).astype(bool)
    ].tolist()

    _save_state({
        "status": "running",
        "stage": "completion_delivery_validation",
        "completed_completion_sessions": 0,
        "total_completion_sessions": len(candidates),
        "cache_entries": len(cache),
    })

    completed = 0
    for idx in candidates:
        row = sessions.loc[idx]
        week = row.get("week")
        cid = row.get("conversation_id")
        det_received = bool(row.get("completion_code_received", False))
        det_code = _safe_text(row.get("completion_code")) or None

        sessions.at[idx, "completion_code_received_deterministic"] = det_received
        sessions.at[idx, "completion_code_deterministic"] = det_code

        transcript = _compact_completion_transcript(raw, week, cid, requests)
        if not transcript:
            sessions.at[idx, "completion_validation_status"] = "no_transcript"
            sessions.at[idx, "completion_evidence_source"] = "deterministic_only"
            sessions.at[idx, "review_required"] = True
            completed += 1
            continue

        key = _hash_key("completion_delivery_v1", str(week), str(cid), transcript)
        if key in cache:
            result = cache[key]
        else:
            if provider is None:
                raise RuntimeError(
                    "LLM cache miss while GEMINI_API_KEY is unavailable. "
                    "This session needs semantic completion-code validation. "
                    "Restore the API key or process this dataset once with Gemini so the result is cached."
                )
            try:
                result = provider.classify_completion_delivery(transcript=transcript)
                cache[key] = result
                _save_cache(cache)
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    _save_state({
                        "status": "stopped",
                        "stage": "completion_delivery_validation",
                        "reason": "rate_limit",
                        "error": str(exc),
                        "completed_completion_sessions": completed,
                        "total_completion_sessions": len(candidates),
                        "cache_entries": len(cache),
                    })
                    return sessions, requests, True
                sessions.at[idx, "completion_validation_status"] = "llm_error"
                sessions.at[idx, "completion_evidence_source"] = "deterministic_only"
                sessions.at[idx, "review_required"] = True
                completed += 1
                continue

        audit = _normalize_completion_audit(result)
        llm_received = audit["received"]
        llm_code = audit["code"]
        conf = audit["confidence"]
        high_conf = conf is not None and conf >= LLM_COMPLETION_ACCEPT_THRESHOLD

        sessions.at[idx, "completion_code_received_llm"] = llm_received
        sessions.at[idx, "completion_code_llm"] = llm_code
        sessions.at[idx, "completion_validation_confidence"] = conf
        sessions.at[idx, "completion_validation_evidence"] = audit["evidence"]
        sessions.at[idx, "completion_validation_model"] = audit["model"]

        status = "llm_unclear"
        source = "deterministic_only"
        conflict = False

        if not high_conf or llm_received is None:
            status = "llm_low_confidence_or_unclear"
            conflict = True
        elif det_received:
            if llm_received is True:
                if det_code and llm_code and det_code.strip().casefold() != llm_code.strip().casefold():
                    status = "code_value_conflict"
                    source = "deterministic_conflict_retained"
                    conflict = True
                else:
                    status = "validated_received"
                    source = "deterministic_plus_llm"
                    if not det_code and llm_code:
                        sessions.at[idx, "completion_code"] = llm_code
                        source = "llm_filled_missing_code"
            else:
                status = "delivery_conflict_deterministic_yes_llm_no"
                source = "deterministic_conflict_retained"
                conflict = True
        else:
            if llm_received is False:
                status = "validated_not_received"
                source = "deterministic_plus_llm"
            elif llm_received is True and llm_code:
                # Safe repair: explicit literal code + high-confidence LLM evidence.
                sessions.at[idx, "completion_code_received"] = True
                sessions.at[idx, "completion_code"] = llm_code
                status = "llm_recovered_deterministic_miss"
                source = "llm_validated_repair"
            else:
                status = "llm_says_received_but_no_literal_code"
                source = "deterministic_retained"
                conflict = True

        sessions.at[idx, "completion_validation_status"] = status
        sessions.at[idx, "completion_evidence_source"] = source
        if conflict:
            sessions.at[idx, "review_required"] = True

        completed += 1
        _save_state({
            "status": "running",
            "stage": "completion_delivery_validation",
            "completed_completion_sessions": completed,
            "total_completion_sessions": len(candidates),
            "cache_entries": len(cache),
        })

    # Attach session-level audit evidence to request events for validation/export.
    if not requests.empty and "conversation_id" in requests.columns:
        merge_cols = [
            "week", "conversation_id",
            "completion_code_received_deterministic", "completion_code_deterministic",
            "completion_code_received_llm", "completion_code_llm",
            "completion_validation_confidence", "completion_validation_status",
            "completion_evidence_source",
        ]
        merge_cols = [c for c in merge_cols if c in sessions.columns]
        keys = [c for c in ["week", "conversation_id"] if c in requests.columns and c in sessions.columns]
        if keys:
            payload_cols = [c for c in merge_cols if c not in keys]
            requests = requests.drop(columns=[c for c in payload_cols if c in requests.columns], errors="ignore")
            lookup = sessions[keys + payload_cols].drop_duplicates(keys)
            requests = requests.merge(lookup, on=keys, how="left")

    return sessions, requests, False


def _recompute_followup_recovery(turns: pd.DataFrame) -> pd.DataFrame:
    """Recompute recovery only after final LLM correctness/follow-up labels exist."""
    if turns is None or turns.empty:
        return turns
    out = turns.copy()
    if "followups_to_recovery" not in out.columns:
        out["followups_to_recovery"] = None
    if "recovered" not in out.columns:
        out["recovered"] = None
    if "recovery_source" not in out.columns:
        out["recovery_source"] = None

    answer = out[out["is_answer_attempt"] == True].copy()
    answer["_ts"] = pd.to_datetime(answer.get("timestamp"), errors="coerce")
    group_keys = [c for c in ["week", "conversation_id"] if c in answer.columns]
    if not group_keys:
        return out

    for _, g in answer.groupby(group_keys, sort=False):
        g = g.sort_values("_ts", kind="stable")
        idxs = list(g.index)
        for pos, idx in enumerate(idxs):
            if out.at[idx, "answer_result"] != "Incorrect":
                continue
            followups = 0
            recovered = False
            for idx2 in idxs[pos + 1:]:
                if not bool(out.at[idx2, "is_followup_question"]):
                    break
                followups += 1
                if out.at[idx2, "answer_result"] == "Correct":
                    recovered = True
                    break
            out.at[idx, "followups_to_recovery"] = followups if followups else None
            out.at[idx, "recovered"] = recovered
            out.at[idx, "recovery_source"] = "llm_followup_sequence"
    return out


def _rebuild_summaries_from_enriched_turns(
    sessions: pd.DataFrame,
    questions: pd.DataFrame,
    turns: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    sessions = sessions.copy()
    questions = questions.copy()

    if not turns.empty and not sessions.empty:
        for i, srow in sessions.iterrows():
            g = turns[
                (turns["week"] == srow["week"])
                & (turns["conversation_id"] == srow["conversation_id"])
                & (turns["is_answer_attempt"] == True)
            ]

            sessions.at[i, "serious_answers"] = int((g["seriousness"] == "Serious").sum())
            sessions.at[i, "non_serious_answers"] = int((g["seriousness"] == "Non-serious").sum())
            sessions.at[i, "seriousness_pending_llm"] = int((g["seriousness"] == "Pending LLM").sum())

            sessions.at[i, "correct_answers"] = int((g["answer_result"] == "Correct").sum())
            sessions.at[i, "partially_correct_answers"] = int((g["answer_result"] == "Partially correct").sum())
            sessions.at[i, "incorrect_answers"] = int((g["answer_result"] == "Incorrect").sum())
            sessions.at[i, "unclear_answers"] = int((g["answer_result"] == "Unclear").sum())

            eval_g = g[g["answer_result"].isin(["Correct", "Partially correct", "Incorrect"])]
            sessions.at[i, "incorrect_rate_among_classified"] = (
                float((eval_g["answer_result"] == "Incorrect").mean()) if len(eval_g) else None
            )

            # Q5: N means observed answers/attempts, not an LLM-created substantiveness label.
            sessions.at[i, "work_count_for_threshold"] = int(len(g))
            sessions.at[i, "work_count_basis"] = "answer_attempts_observed"

            pending_followup = int(g["is_followup_question"].isna().sum()) if "is_followup_question" in g.columns else len(g)
            sessions.at[i, "semantic_fields_pending"] = int(
                (g["seriousness"] == "Pending LLM").sum()
                + (g["answer_result"] == "Unclear").sum()
                + (g["question_type"] == "Pending LLM").sum()
                + pending_followup
            )
            sessions.at[i, "review_required"] = bool(g["review_required"].any() if len(g) else False)

            if "is_followup_question" in g.columns:
                sessions.at[i, "followup_questions"] = int(g["is_followup_question"].eq(True).sum())

    if not questions.empty and not turns.empty:
        from processor import normalize_question
        turns2 = turns[turns["is_answer_attempt"] == True].copy()
        turns2["_question_norm"] = turns2["assistant_question"].map(normalize_question)

        for i, qrow in questions.iterrows():
            qnorm = normalize_question(qrow["question_text"])
            g = turns2[turns2["_question_norm"] == qnorm]
            if not len(g):
                continue

            qtypes = g["question_type"].dropna()
            if len(qtypes):
                mode = qtypes.mode()
                if len(mode):
                    questions.at[i, "question_type"] = mode.iloc[0]
                    if (g["question_type_source"] == "gemini_llm").any():
                        questions.at[i, "question_type_source"] = "gemini_llm"

            questions.at[i, "correct_count"] = int((g["answer_result"] == "Correct").sum())
            questions.at[i, "partial_count"] = int((g["answer_result"] == "Partially correct").sum())
            questions.at[i, "incorrect_count"] = int((g["answer_result"] == "Incorrect").sum())
            questions.at[i, "unclear_count"] = int((g["answer_result"] == "Unclear").sum())

            eval_g = g[g["answer_result"].isin(["Correct", "Partially correct", "Incorrect"])]
            questions.at[i, "incorrect_rate_among_classified"] = (
                float((eval_g["answer_result"] == "Incorrect").mean()) if len(eval_g) else None
            )
            questions.at[i, "review_required"] = bool(g["review_required"].any() if len(g) else False)

    return sessions, questions


def process_uploaded_files(files, use_llm: bool = True, force_reprocess: bool = False):
    """
    Hybrid pipeline with persistent Excel caching.

    Returns:
        raw, turns, sessions, questions, code_request_events

    If the same input files and analysis settings were already processed
    successfully, the saved Excel workbook is loaded immediately.
    """
    files = list(files)
    signature = _processed_signature(files, use_llm=use_llm)

    if not force_reprocess:
        saved = _load_processed_workbook(signature)
        if saved is not None:
            _save_state({
                "status": "complete",
                "stage": "loaded_saved_excel",
                "source": "processed_excel_cache",
                "processed_workbook": str(_processed_workbook_path(signature)),
            })
            return saved

    raw, turns, sessions, questions, requests = _process_uploaded_files_base(files)
    cache = _load_cache()

    if not use_llm:
        saved_path = _save_processed_workbook(
            signature, raw, turns, sessions, questions, requests
        )
        _save_state({
            "status": "complete",
            "stage": "finished_without_llm",
            "cache_entries": len(cache),
            "processed_workbook": str(saved_path),
        })
        return raw, turns, sessions, questions, requests

    provider = get_llm_provider()
    if provider is None:
        _save_state({
            "status": "cache_only",
            "stage": "using_llm_cache_without_api_key",
            "cache_entries": len(cache),
            "prompt_version": TURN_PROMPT_VERSION,
        })

    sessions, requests, completion_stopped = _audit_completion_delivery(
        raw, sessions, requests, provider, cache
    )
    if completion_stopped:
        return raw, turns, sessions, questions, requests

    turns, turn_stopped = _semantic_enrich_turns(turns, provider, cache)
    turns = _recompute_followup_recovery(turns)
    sessions, questions = _rebuild_summaries_from_enriched_turns(
        sessions, questions, turns
    )

    if turn_stopped:
        return raw, turns, sessions, questions, requests

    saved_path = _save_processed_workbook(
        signature, raw, turns, sessions, questions, requests
    )

    _save_state({
        "status": "complete",
        "stage": "finished_cache_only" if provider is None else "finished",
        "cache_entries": len(cache),
        "processed_workbook": str(saved_path),
    })

    return raw, turns, sessions, questions, requests

