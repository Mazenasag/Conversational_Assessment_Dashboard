"""
llm_provider.py

Provider-agnostic LLM layer for the Conversational Assessment Dashboard.

Supported providers:
    gemini
    openai
    groq
    together
    openai_compatible
    anthropic

Recommended .env:
    APP_MODE=local
    LLM_PROVIDER=gemini
    LLM_API_KEY=your_key_here
    LLM_MODEL=gemini-3.5-flash-lite

Optional for custom OpenAI-compatible services:
    LLM_BASE_URL=https://your-provider.example/v1

Backward compatibility:
    GEMINI_API_KEY and GEMINI_MODEL are still accepted when LLM_PROVIDER=gemini.

Important:
- 429/rate-limit/quota errors are raised immediately and are NOT retried.
- Other temporary errors retry at most once by default.
- All providers return the same dictionary shape to the rest of the project.
"""

import json
import os
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv


# The launcher starts from the project root, so this loads the local .env.
load_dotenv()


# -----------------------------------------------------------------------------
# Shared prompts: research/classification logic is provider-independent.
# -----------------------------------------------------------------------------

TURN_SYSTEM_PROMPT = r"""
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


COMPLETION_SYSTEM_PROMPT = """
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


class LLMProvider:
    """Provider-independent interface used by the rest of the project."""

    source_name = "llm"

    def __init__(self, model: str, max_retries: int = 2):
        if not model:
            raise RuntimeError(
                "LLM_MODEL was not found. Add LLM_MODEL=<model-name> to your .env file."
            )
        self.model = model
        self.max_retries = max_retries

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)

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

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        text = (text or "").strip()
        if text.startswith("```"):
            text = (
                text.replace("```json", "")
                .replace("```JSON", "")
                .replace("```", "")
                .strip()
            )
        return text

    def _attach_meta(self, result: Dict[str, Any]) -> Dict[str, Any]:
        result["_meta"] = {
            "source": self.source_name,
            "model": self.model,
        }
        return result

    def _call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        raise NotImplementedError

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
        return self._call_json(TURN_SYSTEM_PROMPT, user_prompt)

    def classify_completion_delivery(self, transcript: str) -> Dict[str, Any]:
        """Audit whether a completion code was actually delivered in a session."""

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
        return self._call_json(COMPLETION_SYSTEM_PROMPT, user_prompt)


class GeminiProvider(LLMProvider):
    source_name = "gemini_llm"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 2,
    ):
        super().__init__(model=model, max_retries=max_retries)

        if not api_key:
            raise RuntimeError("LLM_API_KEY was not found in .env")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "google-genai is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self._types = types
        self.client = genai.Client(api_key=api_key)

    def _fallback_json_call(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> Dict[str, Any]:
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

        return self._attach_meta(json.loads(response_text))

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

                return self._attach_meta(json.loads(response_text))

            except Exception as exc:
                last_error = exc

                if self._is_rate_limit_error(exc):
                    raise

                error_text = str(exc).lower()
                if (
                    "json" in error_text
                    or "response_mime_type" in error_text
                    or "response schema" in error_text
                ):
                    try:
                        return self._fallback_json_call(system_prompt, user_prompt)
                    except Exception as fallback_exc:
                        if self._is_rate_limit_error(fallback_exc):
                            raise
                        last_error = fallback_exc

                if attempt < self.max_retries - 1:
                    time.sleep(1.5)

        raise RuntimeError(
            f"Gemini classification failed after {self.max_retries} attempts: {last_error}"
        )


class OpenAICompatibleProvider(LLMProvider):
    """Works with OpenAI and OpenAI-compatible APIs such as Groq and Together."""

    def __init__(
        self,
        api_key: str,
        model: str,
        provider_name: str = "openai",
        base_url: Optional[str] = None,
        max_retries: int = 2,
    ):
        super().__init__(model=model, max_retries=max_retries)

        if not api_key:
            raise RuntimeError("LLM_API_KEY was not found in .env")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self.provider_name = provider_name
        self.source_name = f"{provider_name}_llm"
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def _request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        json_mode: bool,
    ) -> str:
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    def _call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        last_error = None

        for attempt in range(self.max_retries):
            try:
                try:
                    response_text = self._request(
                        system_prompt, user_prompt, temperature, json_mode=True
                    )
                except Exception as json_mode_exc:
                    if self._is_rate_limit_error(json_mode_exc):
                        raise
                    # Some OpenAI-compatible providers/models do not support response_format.
                    response_text = self._request(
                        system_prompt
                        + "\nReturn ONLY one valid JSON object. No markdown or extra text.",
                        user_prompt,
                        temperature,
                        json_mode=False,
                    )

                response_text = self._strip_code_fences(response_text)
                if not response_text:
                    raise ValueError(f"{self.provider_name} returned an empty response.")

                return self._attach_meta(json.loads(response_text))

            except Exception as exc:
                last_error = exc
                if self._is_rate_limit_error(exc):
                    raise
                if attempt < self.max_retries - 1:
                    time.sleep(1.5)

        raise RuntimeError(
            f"{self.provider_name} classification failed after "
            f"{self.max_retries} attempts: {last_error}"
        )


class AnthropicProvider(LLMProvider):
    source_name = "anthropic_llm"

    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 2,
    ):
        super().__init__(model=model, max_retries=max_retries)

        if not api_key:
            raise RuntimeError("LLM_API_KEY was not found in .env")

        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self.client = Anthropic(api_key=api_key)

    def _call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        last_error = None

        for attempt in range(self.max_retries):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=1200,
                    temperature=temperature,
                    system=(
                        system_prompt
                        + "\nReturn ONLY one valid JSON object. "
                        "Do not use markdown, code fences, commentary, or extra text."
                    ),
                    messages=[{"role": "user", "content": user_prompt}],
                )

                text_parts = [
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                ]
                response_text = self._strip_code_fences("".join(text_parts))
                if not response_text:
                    raise ValueError("Anthropic returned an empty response.")

                return self._attach_meta(json.loads(response_text))

            except Exception as exc:
                last_error = exc
                if self._is_rate_limit_error(exc):
                    raise
                if attempt < self.max_retries - 1:
                    time.sleep(1.5)

        raise RuntimeError(
            f"Anthropic classification failed after {self.max_retries} attempts: {last_error}"
        )


_PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
}


def get_llm_provider() -> Optional[LLMProvider]:
    """
    Build the configured provider.

    Preferred generic variables:
        LLM_PROVIDER
        LLM_API_KEY
        LLM_MODEL
        LLM_BASE_URL   (optional)

    For backward compatibility, Gemini can still use:
        GEMINI_API_KEY
        GEMINI_MODEL
    """

    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    # Backward compatibility with the original Gemini-only .env.
    if provider == "gemini":
        api_key = os.getenv("LLM_API_KEY") or os.getenv("GEMINI_API_KEY")
        model = os.getenv("LLM_MODEL") or os.getenv("GEMINI_MODEL")
    else:
        api_key = os.getenv("LLM_API_KEY")
        model = os.getenv("LLM_MODEL")

    if not api_key:
        return None

    if not model:
        raise RuntimeError(
            "LLM_MODEL was not found. Add LLM_MODEL=<model-name> to your .env file."
        )

    if provider == "gemini":
        return GeminiProvider(api_key=api_key, model=model)

    if provider in {"openai", "groq", "together", "openai_compatible"}:
        base_url = os.getenv("LLM_BASE_URL") or _PROVIDER_BASE_URLS.get(provider)
        if provider == "openai_compatible" and not base_url:
            raise RuntimeError(
                "LLM_BASE_URL is required when LLM_PROVIDER=openai_compatible."
            )
        return OpenAICompatibleProvider(
            api_key=api_key,
            model=model,
            provider_name=provider,
            base_url=base_url,
        )

    if provider == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)

    raise RuntimeError(
        "Unsupported LLM_PROVIDER. Use one of: "
        "gemini, openai, groq, together, openai_compatible, anthropic."
    )
