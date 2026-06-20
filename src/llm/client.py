import anthropic
from pydantic import BaseModel

import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


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

    import json
    return SmokeTestResponse(**json.loads(raw))
