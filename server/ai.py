"""Second opinion on alerts from a vision model, and per-student report summaries.

Active only when ANTHROPIC_API_KEY is set (server/.env). Screenshots then leave the LAN."""
import os

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

MODEL = os.environ.get("INVIGILAI_AI_MODEL", "claude-opus-5-5")
# On a safety decline the API re-runs the request on a fallback model it picks.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 8000  # thinking is always on for this model and counts toward this
EFFORT = "medium"  # this model's default, set explicitly

ENABLED = bool(os.environ.get("ANTHROPIC_API_KEY"))
client = anthropic.AsyncAnthropic() if ENABLED else None


class Verdict(BaseModel):
    violation: bool
    type: str = Field(description="short category, e.g. ai_chatbot, messaging, remote_access, "
                                  "other_app, multiple_screens, no_violation_visible")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(description="one or two sentences about what is visible")


VERDICT_SYSTEM = (
    "You review evidence for an exam-integrity system used in school computer labs. "
    "Each request has one screenshot of a student's screen, taken when an automatic rule fired, "
    "and the rule's description. Decide whether the screenshot itself shows the rule being broken. "
    "Judge only what is visible: the rule may have fired on data the screenshot cannot show "
    "(for example a background process), so say no violation is visible in that case rather than guessing. "
    "Screenshots are small and compressed; lower your confidence when details are unreadable."
)

SUMMARY_SYSTEM = (
    "You write short, neutral summaries of one student's exam-integrity alerts for a teacher. "
    "Use exactly three lines of plain text, no bullets or headings. Line 1: overall picture and "
    "final risk level. Line 2: the most significant events with times. Line 3: what the AI review "
    "of screenshots concluded and any likely false alarms. Do not speculate beyond the data."
)


class AIError(Exception):
    pass


async def _parse(**kwargs):
    try:
        response = await client.beta.messages.parse(
            model=MODEL, max_tokens=MAX_TOKENS, betas=[FALLBACK_BETA], fallbacks="default",
            output_config={"effort": EFFORT},  # merged by parse() with the output_format schema
            **kwargs,
        )
    except anthropic.AuthenticationError as e:
        raise AIError("invalid ANTHROPIC_API_KEY") from e
    except anthropic.PermissionDeniedError as e:
        raise AIError(f"API key lacks permission: {e.message}") from e
    except anthropic.BadRequestError as e:
        raise AIError(f"bad request: {e.message}") from e
    except anthropic.RateLimitError as e:
        raise AIError("rate limited by the API (retries exhausted)") from e
    except anthropic.APIStatusError as e:
        raise AIError(f"API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AIError("could not reach the API (network)") from e
    if response.stop_reason == "refusal":
        raise AIError("the model declined to review this request")
    if response.stop_reason == "max_tokens":
        raise AIError("response was cut off (max_tokens)")
    return response


async def review_screenshot(jpeg_b64, rule, detail, profile):
    """Return a Verdict for one alert screenshot. Raises AIError."""
    response = await _parse(
        system=VERDICT_SYSTEM,
        output_format=Verdict,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": jpeg_b64}},
                {"type": "text", "text": (
                    f"Exam profile: {profile}\nRule that fired: {rule}\nRule detail: {detail}\n"
                    "Does this screenshot show that violation?"
                )},
            ],
        }],
    )
    verdict = response.parsed_output
    if verdict is None:
        raise AIError("no structured output in the response")
    return verdict


class Summary(BaseModel):
    lines: list[str] = Field(description="exactly three lines")


async def summarize(device_id, profile, final_risk, level, alerts):
    """Three-line summary of one student's session. Raises AIError."""
    timeline = "\n".join(
        f"- {a['time']} {a['severity']} {a['rule']}: {a['detail']}"
        f" | AI review: {a['ai']}"
        for a in alerts
    ) or "- no alerts"
    response = await _parse(
        system=SUMMARY_SYSTEM,
        output_format=Summary,
        messages=[{"role": "user", "content": (
            f"Student computer: {device_id}\nExam profile: {profile}\n"
            f"Final risk score: {final_risk:.0f}/100 ({level})\nAlerts in time order:\n{timeline}"
        )}],
    )
    summary = response.parsed_output
    if summary is None or not summary.lines:
        raise AIError("no summary in the response")
    return "\n".join(line.strip() for line in summary.lines[:3])
