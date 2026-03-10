import re

SPAN_ORIGIN = "auto.ai.pydantic_ai"

# Matches data URLs with base64-encoded content, e.g. "data:image/png;base64,iVBORw0K..."
# Group 1: MIME type (e.g. "image/png"), Group 2: base64 data
DATA_URL_BASE64_REGEX = re.compile(
    r"^data:([a-zA-Z0-9][a-zA-Z0-9.+\-]*/[a-zA-Z0-9][a-zA-Z0-9.+\-]*);base64,([A-Za-z0-9+/\-_]+={0,2})$"
)
