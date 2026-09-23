
import re
import hashlib
from collections import defaultdict
from urllib.parse import unquote
import pandas as pd

# ----------------------------
# Structural / objective layer
# ----------------------------

REQUIRED_COLUMNS = {
    "Conversation", "Conversation Id", "Id", "Message",
    "Message Id", "Role", "Timestamp", "User Id"
}

# A code is only extracted when it appears near an explicit code cue in an assistant message.
# This deliberately avoids assuming one specific code family such as BOLD-COFFEE-37.
CODE_TOKEN = r"[A-Z0-9]+(?:[-_][A-Z0-9]+){1,6}"
CODE_CUE_RE = re.compile(
    rf"(?:completion|access)\s+code(?:\s+is)?\s*[:\-]?\s*(?P<code>{CODE_TOKEN})|"
    rf"(?:here(?:'s| is)|your)\s+(?:completion\s+)?code(?:\s+is)?\s*[:\-]?\s*(?P<code2>{CODE_TOKEN})",
    re.I
)

# Provisional request detection used only when no LLM/API annotation is available.
# It is intentionally broad and its provenance is stored.
REQUEST_TERM_RE = re.compile(r"\b(?:completion|access)\s+code\b|\bcode\b", re.I)
REQUEST_INTENT_RE = re.compile(
    r"\b(?:give|tell|show|send|get|have|need|want|provide|receive|ready|finish|end)\b|"
    r"\b(?:can|could|may|please|plz)\b",
    re.I
)

QUESTION_MARKER_RE = re.compile(r"(^|\n)\s*Question\s*:?\s*", re.I)
WRAPUP_PHRASES = (
    "would you like to continue", "would you like a brief summary",
    "would you like to explore", "shall we conclude", "shall we wrap up",
    "is there anything specific", "are you ready to begin"
)

def clean_text(x):
    return re.sub(r"\s+", " ", str(x or "")).strip()

def validate_raw_columns(df):
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))

def is_assessment_question(text):
    """Structural question detection. Conservative and independent of correctness/meaning."""
    t = str(text or "").strip()
    if not t:
        return False
    if QUESTION_MARKER_RE.search(t):
        return True
    if "?" not in t:
        return False
    low = t.lower()
    if any(p in low for p in WRAPUP_PHRASES) and low.count("?") == 1:
        return False
    # Do not count a pure completion-code status prompt as an assessment question.
    if ("completion code" in low or "access code" in low) and low.count("?") == 1 and len(low) < 240:
        return False
    return True

def extract_question(text):
    t = str(text or "").strip()
    matches = list(QUESTION_MARKER_RE.finditer(t))
    if matches:
        return t[matches[-1].end():].strip()
    if "?" in t:
        parts = [p.strip() for p in re.split(r"\n+", t) if p.strip()]
        qs = [p for p in parts if "?" in p]
        if qs:
            return qs[-1]
    return t

def extract_completion_code(text):
    t = str(text or "")
    m = CODE_CUE_RE.search(t)
    if not m:
        return False, None, "deterministic"
    code = m.group("code") or m.group("code2")
    return True, code.upper() if code else None, "deterministic"

def provisional_code_request(text, next_assistant_text=""):
    """
    Conservative fallback when an LLM/API is not connected.

    Important: bare 'code' is common in programming answers, so it is NOT enough
    to mark a request unless the message is short/request-like or the assistant
    explicitly acknowledges it as a completion-code request.
    """
    t = clean_text(text)
    low = t.lower()
    low_next = clean_text(next_assistant_text).lower()
    token_count = len(t.split())

    explicit_completion_term = bool(
        re.search(r"\b(?:completion|access)\s+code\b", t, re.I)
    )
    explicit_request_language = bool(REQUEST_INTENT_RE.search(t))

    # Direct completion/access-code request.
    if explicit_completion_term and explicit_request_language:
        return True, 0.95, "provisional_rule", False

    # Very short bare-code request: "code", "code plz", "give me code".
    short_bare_code = bool(
        token_count <= 6
        and re.search(r"\bcode\b", t, re.I)
        and (
            explicit_request_language
            or re.fullmatch(r"(?:the\s+)?code(?:\s+(?:please|plz))?", low)
        )
    )
    if short_bare_code:
        return True, 0.88, "provisional_rule", True

    # Context fallback: assistant explicitly interprets the student's short message
    # as a completion-code request.
    assistant_ack = (
        "asked for the completion code" in low_next
        or "asking for the completion code" in low_next
        or "asking about the code" in low_next
        or "requested the completion code" in low_next
    )
    if assistant_ack and token_count <= 12 and re.search(r"\bcode\b", t, re.I):
        return True, 0.82, "provisional_context_rule", True

    return False, 0.90, "provisional_rule", False

# ----------------------------
# Semantic layer (API-ready)
# ----------------------------

def provisional_seriousness(text, is_answer_attempt, is_code_request):
    """
    Do not force almost every response to 'Serious'.
    Without an LLM, only obvious non-serious content is classified.
    Everything else remains 'Pending LLM'.
    """
    if not is_answer_attempt or is_code_request:
        return "Not applicable", None, "deterministic", False

    t = clean_text(text)
    low = t.lower().strip(" .!?\\")
    if not t:
        return "Non-serious", 0.99, "provisional_rule", False
    if re.fullmatch(r"(.)\1{3,}", low) or low in {"asdf", "asdfgh", "qwerty", "blah", "random"}:
        return "Non-serious", 0.98, "provisional_rule", False
    return "Pending LLM", None, "pending_llm", True

def conservative_correctness(feedback):
    """
    Use only explicit assistant feedback as deterministic correctness evidence.

    This deliberately does NOT judge the student's academic answer itself.  It
    only locks a label when the chatbot's feedback clearly evaluates the answer.
    Everything ambiguous remains Unclear for the LLM.

    Important dashboard rule: a correct multiple-choice selection is Correct
    even when the student only enters the option letter.  Missing explanation or
    brevity must not be converted into Partially correct unless the assistant
    explicitly says the academic answer is partial/incomplete.
    """
    t = clean_text(feedback)
    low = t.lower()
    if not t:
        return "Unclear", "pending_llm", True

    # Partial must be checked first because feedback can contain words such as
    # "correct" while explicitly saying only part of the answer is correct.
    partial_patterns = (
        r"\bpartially correct\b",
        r"\bpartly correct\b",
        r"\bpartially right\b",
        r"\bpartly right\b",
        r"\bmostly correct\b",
        r"\bright direction,? but\b",
        r"\bon the right track,? but\b",
    )
    if any(re.search(p, low) for p in partial_patterns):
        return "Partially correct", "deterministic_text_evidence", False

    # An explicit positive opening is authoritative. This prevents feedback such as
    # "Correct! The correct answer is C ..." from being misread by later negative
    # phrase detectors that merely mention the words "correct answer".
    if re.search(r"^(?:correct|exactly)\b", low):
        return "Correct", "deterministic_text_evidence", False

    incorrect_patterns = (
        r"^incorrect\b",
        r"^not quite\b",
        r"\bthat's incorrect\b",
        r"\bthat is incorrect\b",
        r"\bthat's not correct\b",
        r"\bthat is not correct\b",
        r"\bnot quite correct\b",
        r"\bthe correct answer is\b",
        r"\bthe correct option is\b",
    )
    if any(re.search(p, low[:320]) for p in incorrect_patterns):
        return "Incorrect", "deterministic_text_evidence", False

    correct_patterns = (
        r"^correct\b",
        r"^exactly\b",
        r"^yes[,! ]+.*\bcorrect\b",
        r"^nice[,! —-]+.*\bcorrect\b",
        r"^good[,! —-]+.*\bcorrect\b",
        r"\bthat's correct\b",
        r"\bthat is correct\b",
        r"\bthat's right\b",
        r"\bthat is right\b",
        r"\bexactly right\b",
        r"\b[a-d] is (?:correct|right)\b",
    )
    if any(re.search(p, low[:320]) for p in correct_patterns):
        return "Correct", "deterministic_text_evidence", False

    return "Unclear", "pending_llm", True

def provisional_question_type(question):
    """
    Q4 requires a semantic Coding-vs-Conceptual judgement.

    Do not infer content type from format or isolated keywords. A multiple-choice
    question can be Coding or Conceptual, and a conceptual question may mention
    code/package names. Therefore every academic question is routed to the LLM.
    """
    if not clean_text(question):
        return "Not applicable", "deterministic", False
    return "Pending LLM", "pending_llm", True


def normalize_question(q):
    x = clean_text(q).lower()
    x = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", x)
    return re.sub(r"\s+", " ", x)

# ----------------------------
# Conversation reconstruction
# ----------------------------

def process_dataframe(df, week=1, source_file=None):
    validate_raw_columns(df)
    df = df.copy()
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df = df.sort_values(["Conversation Id", "Timestamp", "Id"], kind="stable").reset_index(drop=True)

    turns = []
    sessions = []
    request_events = []

    for cid, g in df.groupby("Conversation Id", sort=False):
        msgs = g.to_dict("records")
        user_id = msgs[0]["User Id"] if msgs else None
        conv_turns = []

        # Pre-compute question counts at each message position.
        q_count_before = {}
        count = 0
        for i, m in enumerate(msgs):
            q_count_before[i] = count
            if m["Role"] == "assistant" and is_assessment_question(m["Message"]):
                count += 1

        # Pre-compute completion events in the session.
        completion_events = []
        for i, m in enumerate(msgs):
            if m["Role"] != "assistant":
                continue
            ok, code, source = extract_completion_code(m["Message"])
            if ok:
                completion_events.append({
                    "message_index": i,
                    "timestamp": m["Timestamp"],
                    "code": code,
                    "questions_before_code": q_count_before[i],
                    "source": source,
                })

        request_number = 0

        for i, m in enumerate(msgs):
            if m["Role"] != "user":
                continue

            prev_i = i - 1 if i > 0 and msgs[i-1]["Role"] == "assistant" else None
            next_i = i + 1 if i + 1 < len(msgs) and msgs[i+1]["Role"] == "assistant" else None
            prev_text = msgs[prev_i]["Message"] if prev_i is not None else ""
            next_text = msgs[next_i]["Message"] if next_i is not None else ""
            prev_is_q = is_assessment_question(prev_text) if prev_i is not None else False

            is_request, req_conf, req_source, req_review = provisional_code_request(
                m["Message"], next_text
            )
            answer_attempt = bool(prev_is_q and not is_request)

            seriousness, s_conf, s_source, s_review = provisional_seriousness(
                m["Message"], answer_attempt, is_request
            )
            result, result_source, result_review = (
                conservative_correctness(next_text) if answer_attempt
                else ("Not applicable", "deterministic", False)
            )
            q_text = extract_question(prev_text) if prev_is_q else ""
            q_type, q_source, q_review = (
                provisional_question_type(q_text) if q_text
                else ("Not applicable", "deterministic", False)
            )
            code_in_feedback, code_value, code_source = extract_completion_code(next_text)

            turn = {
                "week": week,
                "source_file": source_file,
                "student_id": user_id,
                "conversation_id": cid,
                "user_message_id": m.get("Message Id"),
                "timestamp": m["Timestamp"],
                "questions_asked_so_far": q_count_before[i],
                "assistant_question": q_text,
                "student_answer": m["Message"],
                "assistant_feedback": next_text,
                "is_answer_attempt": answer_attempt,

                "completion_code_requested": is_request,
                "code_request_confidence": req_conf,
                "code_request_source": req_source,
                "code_request_review_required": req_review,

                "seriousness": seriousness,
                "seriousness_confidence": s_conf,
                "seriousness_source": s_source,
                "seriousness_review_required": s_review,

                "answer_result": result,
                "answer_result_source": result_source,
                "answer_result_review_required": result_review,

                "question_type": q_type,
                "question_type_source": q_source,
                "question_type_review_required": q_review,

                "completion_code_provided_in_feedback": code_in_feedback,
                "completion_code": code_value,
                "completion_code_source": code_source if code_in_feedback else None,

                "followups_to_recovery": None,
                "recovered": None,
                "recovery_source": None,
                "review_required": any([req_review, s_review, result_review, q_review]),
            }
            conv_turns.append(turn)

            if is_request:
                request_number += 1

                future_codes = [c for c in completion_events if c["message_index"] > i]
                immediate_code = None
                if next_i is not None:
                    ok, code, _ = extract_completion_code(next_text)
                    if ok:
                        immediate_code = code

                if immediate_code:
                    observed_outcome = "Provided immediately"
                    later_code = immediate_code
                    turns_until_code = 0
                elif future_codes:
                    observed_outcome = "Not provided immediately; provided later"
                    later = future_codes[0]
                    later_code = later["code"]
                    # Count user messages after this request before the code.
                    turns_until_code = sum(
                        1 for j in range(i + 1, later["message_index"])
                        if msgs[j]["Role"] == "user"
                    )
                else:
                    observed_outcome = "Not provided in session"
                    later_code = None
                    turns_until_code = None

                request_events.append({
                    "week": week,
                    "source_file": source_file,
                    "student_id": user_id,
                    "conversation_id": cid,
                    "request_number": request_number,
                    "request_timestamp": m["Timestamp"],
                    "request_text": m["Message"],
                    "questions_before_request": q_count_before[i],
                    "answer_attempts_before_request": sum(
                        1 for t in conv_turns[:-1] if t["is_answer_attempt"]
                    ),
                    "assistant_response_to_request": next_text,
                    "observed_request_outcome": observed_outcome,
                    "completion_code_after_request": later_code,
                    "user_turns_until_code": turns_until_code,
                    "request_detection_source": req_source,
                    "request_detection_confidence": req_conf,
                    "review_required": bool(req_review),
                })

        # Recovery: only when both the initiating incorrect label and later correct label
        # are high-confidence observed feedback labels. Otherwise leave unresolved.
        answer_positions = [idx for idx, t in enumerate(conv_turns) if t["is_answer_attempt"]]
        for p, idx in enumerate(answer_positions):
            t = conv_turns[idx]
            if t["answer_result"] != "Incorrect":
                continue
            followups = 0
            recovered = False
            for idx2 in answer_positions[p+1:]:
                followups += 1
                if conv_turns[idx2]["answer_result"] == "Correct":
                    recovered = True
                    break
            t["followups_to_recovery"] = followups if recovered else None
            t["recovered"] = recovered
            t["recovery_source"] = "deterministic_sequence_over_explicit_feedback"

        turns.extend(conv_turns)

        answer_turns = [t for t in conv_turns if t["is_answer_attempt"]]
        evaluated = [t for t in answer_turns if t["answer_result"] in {"Correct","Partially correct","Incorrect"}]
        incorrect = [t for t in answer_turns if t["answer_result"] == "Incorrect"]
        recovery = [t["followups_to_recovery"] for t in incorrect if t["followups_to_recovery"] is not None]

        requests = [e for e in request_events if e["conversation_id"] == cid and e["week"] == week]
        first_request = requests[0] if requests else None

        serious_n = sum(t["seriousness"] == "Serious" for t in answer_turns)
        non_serious_n = sum(t["seriousness"] == "Non-serious" for t in answer_turns)
        seriousness_pending = sum(t["seriousness"] == "Pending LLM" for t in answer_turns)

        correct_n = sum(t["answer_result"] == "Correct" for t in answer_turns)
        partial_n = sum(t["answer_result"] == "Partially correct" for t in answer_turns)
        incorrect_n = sum(t["answer_result"] == "Incorrect" for t in answer_turns)
        unclear_n = sum(t["answer_result"] == "Unclear" for t in answer_turns)

        completed = bool(completion_events)

        if completed:
            non_completion_reason = "Completed"
            reason_source = "deterministic"
        elif requests:
            non_completion_reason = "Requested but no code issued"
            reason_source = "deterministic_observed"
        else:
            non_completion_reason = "No code issued; semantic reason pending"
            reason_source = "pending_llm"

        sessions.append({
            "week": week,
            "source_file": source_file,
            "student_id": user_id,
            "conversation_id": cid,
            "session_start": msgs[0]["Timestamp"] if msgs else None,
            "session_end": msgs[-1]["Timestamp"] if msgs else None,

            "total_user_messages": sum(m["Role"] == "user" for m in msgs),
            "total_assistant_messages": sum(m["Role"] == "assistant" for m in msgs),
            "total_questions_asked": sum(
                m["Role"] == "assistant" and is_assessment_question(m["Message"])
                for m in msgs
            ),
            "answer_attempts": len(answer_turns),

            "serious_answers": serious_n,
            "non_serious_answers": non_serious_n,
            "seriousness_pending_llm": seriousness_pending,

            "correct_answers": correct_n,
            "partially_correct_answers": partial_n,
            "incorrect_answers": incorrect_n,
            "unclear_answers": unclear_n,
            "incorrect_rate_among_classified": incorrect_n / len(evaluated) if evaluated else None,

            "code_request_count": len(requests),
            "completion_code_requested": bool(requests),
            "first_code_request_after_questions": first_request["questions_before_request"] if first_request else None,
            "answer_attempts_before_first_request": first_request["answer_attempts_before_request"] if first_request else None,

            "completion_code_received": completed,
            "completion_code": completion_events[0]["code"] if completed else None,
            "questions_before_code_received": completion_events[0]["questions_before_code"] if completed else None,

            "avg_followups_to_recovery": sum(recovery)/len(recovery) if recovery else None,
            "max_followups_to_recovery": max(recovery) if recovery else None,

            "non_completion_reason": non_completion_reason,
            "non_completion_reason_source": reason_source,

            # Q5 uses observed answer attempts (N answers), exactly as requested by the instructor.
            # It is intentionally independent of semantic seriousness/correctness labels.
            "work_count_for_threshold": len(answer_turns),
            "work_count_basis": "answer_attempts_observed",
            "semantic_fields_pending": (
                seriousness_pending + unclear_n
                + sum(t["question_type"] == "Pending LLM" for t in answer_turns)
            ),
            "review_required": any(t["review_required"] for t in conv_turns),
        })

    turns_df = pd.DataFrame(turns)
    sessions_df = pd.DataFrame(sessions)
    requests_df = pd.DataFrame(request_events)

    # Question summary
    qrows = []
    if not turns_df.empty:
        valid = turns_df[turns_df["is_answer_attempt"] & turns_df["assistant_question"].astype(bool)].copy()
        valid["question_norm"] = valid["assistant_question"].map(normalize_question)
        valid["question_id"] = valid["question_norm"].map(
            lambda x: "Q-" + hashlib.sha1(x.encode("utf-8")).hexdigest()[:8].upper()
        )
        for qid, group in valid.groupby("question_id", dropna=False):
            eval_g = group[group["answer_result"].isin(["Correct","Partially correct","Incorrect"])]
            inc_g = group[group["answer_result"] == "Incorrect"]
            rv = inc_g["followups_to_recovery"].dropna()
            types = group["question_type"].dropna()
            q_type = types.mode().iloc[0] if len(types) else "Pending LLM"
            qrows.append({
                "week": week,
                "source_file": source_file,
                "question_id": qid,
                "question_text": group.iloc[0]["assistant_question"],
                "question_type": q_type,
                "question_type_source": (
                    "pending_llm" if q_type == "Pending LLM" else "deterministic_structure"
                ),
                "total_attempts": len(group),
                "correct_count": (group["answer_result"] == "Correct").sum(),
                "partial_count": (group["answer_result"] == "Partially correct").sum(),
                "incorrect_count": len(inc_g),
                "unclear_count": (group["answer_result"] == "Unclear").sum(),
                "incorrect_rate_among_classified": (
                    len(inc_g) / len(eval_g) if len(eval_g) else None
                ),
                "avg_followups_to_recovery": rv.mean() if len(rv) else None,
                "review_required": bool(
                    (group["question_type"] == "Pending LLM").any()
                    or (group["answer_result"] == "Unclear").any()
                ),
            })

    questions_df = pd.DataFrame(qrows)
    return turns_df, sessions_df, questions_df, requests_df

# ----------------------------
# Multi-week wrapper
# ----------------------------

def process_uploaded_files(files):
    """
    Process one or more weekly exports.
    Returns:
      raw, turns, sessions, questions, code_request_events
    """
    all_raw, all_turns, all_sessions, all_questions, all_requests = [], [], [], [], []

    for f in files:
        # Browser/Streamlit uploads may expose URL-encoded filenames, e.g.
        # Data%20Viz%20-%20Week%202%20-%20SAMPLE%2010%20sessions.xlsx.
        # Decode first, then derive the TRUE week from the filename.
        name = unquote(str(getattr(f, "name", "")))

        m = re.search(r"\bweek\s*[_\-\s]*(\d+)\b", name, re.I)
        if not m:
            raise ValueError(
                f"Could not determine week number from filename: {name}. "
                "Please include 'Week N' in the filename, for example "
                "'Data Viz - Week 3 - SAMPLE 10 sessions.xlsx'."
            )

        week = int(m.group(1))

        df = pd.read_excel(f)
        raw = df.copy()
        raw.insert(0, "week", week)
        raw.insert(1, "source_file", name)
        all_raw.append(raw)

        t, s, q, r = process_dataframe(df, week=week, source_file=name)
        all_turns.append(t)
        all_sessions.append(s)
        all_questions.append(q)
        all_requests.append(r)

    return (
        pd.concat(all_raw, ignore_index=True) if all_raw else pd.DataFrame(),
        pd.concat(all_turns, ignore_index=True) if all_turns else pd.DataFrame(),
        pd.concat(all_sessions, ignore_index=True) if all_sessions else pd.DataFrame(),
        pd.concat(all_questions, ignore_index=True) if all_questions else pd.DataFrame(),
        pd.concat(all_requests, ignore_index=True) if all_requests else pd.DataFrame(),
    )

# ----------------------------
# Validation layer
# ----------------------------

def build_validation_report(raw, turns, sessions, requests):
    checks = []

    def add(check, passed, detail, severity="error"):
        checks.append({
            "check": check,
            "status": "PASS" if passed else "FAIL",
            "severity": severity,
            "detail": detail,
        })

    # Unique session key
    dup_sessions = sessions.duplicated(["week", "conversation_id"]).sum() if len(sessions) else 0
    add("Unique session rows", dup_sessions == 0, f"Duplicate session rows: {dup_sessions}")

    # Session count consistency
    raw_sessions = raw[["week", "Conversation Id"]].drop_duplicates().shape[0] if len(raw) else 0
    add("Raw/session count match", raw_sessions == len(sessions),
        f"Raw unique sessions={raw_sessions}; Session_Summary rows={len(sessions)}")

    # Request count consistency
    req_sum = int(sessions["code_request_count"].sum()) if len(sessions) and "code_request_count" in sessions else 0
    add("Request-event count match", req_sum == len(requests),
        f"Session request total={req_sum}; Code_Request_Events rows={len(requests)}")

    # Completion evidence
    completed = sessions[sessions["completion_code_received"] == True] if len(sessions) else sessions
    missing_code = completed["completion_code"].isna().sum() if len(completed) else 0
    add("Completion has code evidence", missing_code == 0,
        f"Completed sessions without extracted code: {missing_code}")

    # Matrix invariant support
    if len(sessions):
        asked = int(sessions["completion_code_requested"].sum())
        not_asked = len(sessions) - asked
        add("Asked + did-not-ask = sessions", asked + not_asked == len(sessions),
            f"Asked={asked}; Did not ask={not_asked}; Sessions={len(sessions)}")

    # Semantic pending visibility
    pending = int((turns["review_required"] == True).sum()) if len(turns) else 0
    add("Semantic review tracked", True,
        f"Turn-level rows flagged for LLM/manual review: {pending}", severity="info")

    return pd.DataFrame(checks)
