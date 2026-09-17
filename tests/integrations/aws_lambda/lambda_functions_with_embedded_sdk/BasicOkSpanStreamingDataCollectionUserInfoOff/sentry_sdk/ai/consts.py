import re

# Matches data URLs with base64-encoded content, e.g. "data:image/png;base64,iVBORw0K..."
DATA_URL_BASE64_REGEX = re.compile(
    r"^data:(?:[a-zA-Z0-9][a-zA-Z0-9.+\-]*/[a-zA-Z0-9][a-zA-Z0-9.+\-]*)(?:;[a-zA-Z0-9\-]+=[^;,]*)*;base64,(?:[A-Za-z0-9+/\-_]+={0,2})$"
)
