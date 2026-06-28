from __future__ import annotations

import json
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

import config
from src.documents.models import FilingDocument
from src.llm.allowlist import EnumeratedAllowlist
from src.llm.validator import ValidationMode, ValidationResult, ValidationViolation, validate_output

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Production structured call with retry contract
# ---------------------------------------------------------------------------

def structured_call(
    schema: type[T],
    system_prompt: str,
    user_message: str,
    allowlist: EnumeratedAllowlist,
    document: FilingDocument,
    *,
    mode: ValidationMode = "strict",
    max_tokens: int = 4096,
) -> tuple[T, ValidationResult]:
    """
    Call the Anthropic API using the tool-use pattern and validate the output.

    Retry contract (exactly one retry):
    1. First call → parse via schema.model_validate → validate_output
    2. Pass: return (parsed, vr)
    3. Fail: feed violations back as tool_result → retry once → re-validate
    4. Still fail: return (parsed.model_copy(update={"status": "validation_failed"}), vr)

    Pydantic ValidationError on JSON parse → raised immediately, no retry.
    """
    tool_def = {
        "name": "output",
        "description": "Structured output matching the requested schema.",
        "input_schema": schema.model_json_schema(),
    }

    messages: list[dict] = [{"role": "user", "content": user_message}]
    response = _client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[tool_def],
        tool_choice={"type": "tool", "name": "output"},
        messages=messages,
    )

    tool_block = _find_tool_use(response)
    try:
        parsed: T = schema.model_validate(_coerce_nested_json(tool_block.input))
    except ValidationError as exc:
        # The model produced a structurally valid tool-use call but the content
        # doesn't conform to our schema (e.g., Anthropic serialized nested objects
        # as JSON strings that contain unescapable quotes). Return a parse-failed
        # shell rather than propagating the exception as a crash.
        return _parse_error_shell(schema, exc)

    vr = validate_output(parsed, allowlist, document=document, mode=mode)
    if vr.passed:
        return parsed, vr

    # One retry with violation feedback.
    feedback = _format_violations(vr)
    retry_messages: list[dict] = [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": response.content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": feedback,
                }
            ],
        },
    ]
    retry_response = _client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        tools=[tool_def],
        tool_choice={"type": "tool", "name": "output"},
        messages=retry_messages,
    )

    retry_block = _find_tool_use(retry_response)
    try:
        parsed2: T = schema.model_validate(_coerce_nested_json(retry_block.input))
    except ValidationError as exc:
        return _parse_error_shell(schema, exc)

    vr2 = validate_output(parsed2, allowlist, document=document, mode=mode)
    if vr2.passed:
        return parsed2, vr2

    failed: T = parsed2.model_copy(update={"status": "validation_failed"})
    return failed, vr2


def _parse_error_shell(schema: type[T], exc: ValidationError) -> tuple[T, ValidationResult]:
    """Return a status=validation_failed shell when model_validate fails structurally."""
    shell: T = schema.model_construct(status="validation_failed")
    vr = ValidationResult(
        passed=False,
        violations=[ValidationViolation(
            field_path="tool_block.input",
            raw_token="",
            canonical="",
            reason=f"parse_error: {exc.error_count()} field(s) failed Pydantic schema validation",
        )],
    )
    return shell, vr


def _coerce_nested_json(data: Any) -> Any:
    """
    Recursively parse string values that are valid JSON objects or arrays.

    Anthropic's tool-use API sometimes serializes nested objects as JSON strings
    (e.g., {"headline": '{"text": "...", "citations": [...]}'} instead of a real dict).
    This normalizes the structure before model_validate sees it.
    """
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, (dict, list)):
                return _coerce_nested_json(parsed)
        except (json.JSONDecodeError, ValueError):
            pass
        return data
    if isinstance(data, dict):
        return {k: _coerce_nested_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_coerce_nested_json(item) for item in data]
    return data


def _find_tool_use(response: anthropic.types.Message) -> anthropic.types.ToolUseBlock:
    for block in response.content:
        if block.type == "tool_use":
            return block
    raise ValueError(f"No tool_use block in response: {response.content}")


def _format_violations(vr: ValidationResult) -> str:
    lines = [
        f"VALIDATION_FAILURE: Your response contains {len(vr.violations)} violation(s). "
        "Correct every item below and return a complete corrected response.",
        "",
        "Violations:",
    ]
    for v in vr.violations:
        lines.append(f'  field="{v.field_path}" token="{v.raw_token}" reason="{v.reason}"')
    lines += [
        "",
        "Rules: Do not write any numeric value or year/date in any text field. "
        "Directional words (grew, declined, expanded) are allowed; magnitudes and specific years are not. "
        "To express a magnitude, cite a figure_id. "
        "To express a date, use a verbatim section excerpt that contains it. "
        "Verbatim excerpts must be character-for-character copies from the source text. "
        "Cite only figure_ids present in the catalog for this run — "
        "figure_id_not_in_allowlist means the cited figure_id does not exist "
        "for this company+year; check the catalog and use only listed figure_ids.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Smoke-test stub (kept for backward compatibility)
# ---------------------------------------------------------------------------

class SmokeTestResponse(BaseModel):
    status: str
    message: str


def smoke_call() -> SmokeTestResponse:
    """
    Send a minimal structured call to the Anthropic API and parse the response.

    This validates three things: the API key works, the network can reach
    Anthropic, and our structured JSON parsing pipeline functions. The prompt
    intentionally avoids numbers to test the constraint we enforce in Phase 2+.

    Returns:
        SmokeTestResponse with status="ok" and a short message.

    Raises:
        anthropic.AuthenticationError: Bad or missing API key.
        anthropic.APIConnectionError:  Network unreachable.
        ValidationError:               Model returned JSON that doesn't match schema.
    """
    response = _client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": (
                    'Return a JSON object with exactly two keys: '
                    '"status" (the string "ok") and "message" (one short sentence '
                    'confirming you received this test). Do not include any numbers.'
                ),
            }
        ],
    )

    # The model returns plain text containing JSON — extract and parse it.
    raw = response.content[0].text.strip()

    # Strip markdown code fences if the model wraps its JSON in them.
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return SmokeTestResponse(**json.loads(raw))
