
"""
llm_provider.py

Checkpoint-safe Google Gemini provider.

Required packages:
    google-genai
    python-dotenv

Install:
    pip install -U google-genai python-dotenv

.env:
    GEMINI_API_KEY=your_key_here

Optional:
    GEMINI_MODEL=gemini-2.5-flash-lite

Important:
- 429/rate-limit/quota errors are raised immediately and are NOT retried.
- Other temporary errors retry at most once.
- JSON output is requested with response_mime_type="application/json".
"""

import json
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv


load_dotenv()

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.5-flash-lite",
)


class LLMProvider:
    def classify_turn(
        self,
        question: str,
        answer: str,
        feedback: str = "",
        previous_question: str = "",
        previous_answer: str = "",
        previous_feedback: str = "",
    ) -> Dict[str, Any]:
        raise NotImplementedError



    def classify_completion_delivery(
        self,
        transcript: str,
    ) -> Dict[str, Any]:
        raise NotImplementedError



class GeminiProvider(LLMProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 2,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or DEFAULT_MODEL
        self.max_retries = max_retries

        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY was not found. "
                "Create a .env file containing:\n"
                "GEMINI_API_KEY=your_key_here"
            )

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._types = types
        self.client = genai.Client(api_key=self.api_key)

    def _is_rate_limit_error(self, exc: Exception) -> bool:
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
            or "resource_exhausted" in text
            or "quota exceeded" in text
            or ("quota" in text and "exceed" in text)
        )

    def _strip_code_fences(self, text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
        return text

    def _fallback_json_call(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
        """Fallback without JSON MIME mode, then parse the returned text ourselves."""
        response = self.client.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config=self._types.GenerateContentConfig(
                system_instruction=(
                    system_prompt
                    + "\nReturn ONLY one valid JSON object. "
                      "Do not use markdown, code fences, commentary, or extra text."
                ),
                temperature=0.0,
            ),
        )

        response_text = self._strip_code_fences(response.text or "")
        if not response_text:
            raise ValueError("Gemini fallback returned an empty response.")

        result = json.loads(response_text)
        result["_meta"] = {
            "source": "gemini_llm",
            "model": self.model,
        }
        return result

    def _call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=self._types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=temperature,
                        response_mime_type="application/json",
                    ),
                )

                response_text = response.text or ""
                if not response_text:
                    raise ValueError("Gemini returned an empty response.")

                result = json.loads(response_text)
                result["_meta"] = {
                    "source": "gemini_llm",
                    "model": self.model,
                }
                return result

            except Exception as exc:
                last_error = exc

                # Checkpoint-safe behaviour: do not repeatedly retry quota/rate-limit errors.
                if self._is_rate_limit_error(exc):
                    raise

                error_text = str(exc).lower()

                # If JSON mode or JSON parsing is the problem, try once without
                # strict JSON MIME mode, while still asking for JSON only.
                if (
                    "json" in error_text
                    or "response_mime_type" in error_text
                    or "response schema" in error_text
                ):
                    try:
                        return self._fallback_json_call(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                        )
                    except Exception as fallback_exc:
                        if self._is_rate_limit_error(fallback_exc):
                            raise
                        last_error = fallback_exc

                if attempt < self.max_retries - 1:
                    time.sleep(1.5)

        raise RuntimeError(
            f"Gemini classification failed after {self.max_retries} attempts: "
            f"{last_error}"
        )

    def classify_turn(
        self,
        question: str,
        answer: str,
        feedback: str = "",
        previous_question: str = "",
        previous_answer: str = "",
        previous_feedback: str = "",
    ) -> Dict[str, Any]:
        """Classify only the semantic fields required by instructor Q2a/Q3/Q3a/Q4."""

        system_prompt = r"""
You are a research-data annotator for a higher-education conversational assessment.
Classify ONE student answer turn. Keep the four judgements separate and use only the
evidence specified for each judgement.

1) SERIOUSNESS — use CURRENT QUESTION + STUDENT ANSWER, with SHORT FEEDBACK only as
supporting evidence. Be deliberately generous.

SERIOUS = any plausible genuine attempt to answer, reason, interpret, clarify, guess,
or otherwise engage with the academic task. A response is still SERIOUS when it is:
- wrong or partially wrong;
- incomplete, uncertain, poorly written, or very short;
- only A/B/C/D;
- a short technical word or code fragment such as "geom_point()", "bin size",
  "bracket?", "scatter", or "bar chart";
- "I don't know", "dont know", "not sure", or another uncertainty response;
- missing a requested explanation while still attempting the answer.

SHORT FEEDBACK is the first feedback cue about THIS answer only. If it academically
evaluates the response — e.g. "Correct", "Not quite", "You're close", "Partly right",
"Good try", or explains/corrects the answer — that is strong evidence of genuine
engagement and therefore supports SERIOUS.

NON_SERIOUS is reserved for CLEAR non-engagement only: gibberish/random text, spam,
clearly unrelated conversation, a greeting/admin message instead of an answer, or a
completion-code request instead of attempting the academic task.

NEVER classify an answer as NON_SERIOUS merely because it is wrong, short, incomplete,
uncertain, a single option letter, poorly written, or missing justification.
When uncertain, choose SERIOUS. Only choose NON_SERIOUS when non-engagement is clear.

2) ANSWER_RESULT — use CURRENT QUESTION + STUDENT ANSWER + SHORT FEEDBACK.
Allowed: CORRECT, PARTIALLY_CORRECT, INCORRECT, UNCLEAR.
- Explicit feedback confirming the answer => CORRECT.
- Explicit feedback rejecting the answer => INCORRECT.
- Explicit feedback saying it is partly right, or academic content is genuinely mixed =>
  PARTIALLY_CORRECT.
- UNCLEAR only when evidence is genuinely insufficient/conflicting.
Do not downgrade a correct answer because it is only A/B/C/D, short, poorly written,
or lacks a requested explanation. Correctness and seriousness are independent.

3) QUESTION_TYPE — use CURRENT QUESTION ONLY. Choose exactly CODING or CONCEPTUAL.
CODING = direct reasoning about code, syntax, functions, debugging, program behaviour,
Shiny/R/ggplot, implementation, or code interpretation.
CONCEPTUAL = principles, interpretation, chart/visual choice, design, terminology,
perception, or other non-code conceptual reasoning.
Ignore format: a multiple-choice coding question is still CODING.

4) IS_FOLLOWUP_QUESTION — use PREVIOUS QUESTION + PREVIOUS STUDENT ANSWER + CURRENT
QUESTION. TRUE only when the current question genuinely continues the unresolved previous
exchange by probing, correcting, clarifying, scaffolding, asking for elaboration, or giving
another chance on the same problem. FALSE for a new planned/independent question.
A question is NOT a follow-up merely because it comes immediately after the previous answer
or covers a similar topic.

The SHORT FEEDBACK never contains the next assessment question. Do not infer or judge the
student against any later question.

Return one valid JSON object only. Confidence values must be 0.0 to 1.0.
For routine SERIOUS + CORRECT cases, set reason to an empty string.
Use a brief reason only for NON_SERIOUS, INCORRECT, PARTIALLY_CORRECT, UNCLEAR, or another
case that genuinely needs review/explanation.
"""

        user_prompt = f"""
PREVIOUS TURN (for follow-up determination only):
Previous question: {previous_question}
Previous student answer: {previous_answer}
Previous assistant feedback: {previous_feedback}

CURRENT TURN:
Question: {question}
Student answer: {answer}
Short assistant feedback about this answer only: {feedback}

Return exactly this schema:
{{
  "seriousness": "SERIOUS or NON_SERIOUS",
  "answer_result": "CORRECT or PARTIALLY_CORRECT or INCORRECT or UNCLEAR",
  "question_type": "CODING or CONCEPTUAL",
  "is_followup_question": false,
  "reason": "brief reason only for exceptional/review cases; otherwise empty string",
  "confidence": {{
    "seriousness": 0.0,
    "answer_result": 0.0,
    "question_type": 0.0,
    "is_followup_question": 0.0
  }}
}}
"""
        return self._call_json(system_prompt, user_prompt)



    def classify_completion_delivery(
        self,
        transcript: str,
    ) -> Dict[str, Any]:
        """
        Independently audit whether a completion code was actually delivered in
        a session and extract the literal code when the transcript supports it.
        """

        system_prompt = """
You are validating completion-code delivery in a higher-education conversational assessment.

Review the session transcript carefully. The student may discuss programming code, so do NOT
confuse programming/code examples with the assessment completion/access code.

Determine only from explicit transcript evidence whether the assistant actually provided the
assessment completion/access code. A promise to provide it later, saying the student qualifies,
or discussing the code without giving its literal value does NOT count as received.

If a completion/access code is explicitly supplied, copy the literal code exactly as shown.
Do not invent, normalize, or guess a code. If no literal code is present, completion_code must be null.

Return valid JSON only.
"""

        user_prompt = f"""
SESSION TRANSCRIPT:
{transcript}

Return exactly:
{{
  "code_received": true,
  "completion_code": "literal code exactly as shown, or null",
  "evidence": "short exact supporting phrase or empty string",
  "confidence": 0.0
}}
"""

        return self._call_json(system_prompt, user_prompt)



def get_llm_provider() -> Optional[GeminiProvider]:
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return None

    return GeminiProvider(api_key=api_key)
