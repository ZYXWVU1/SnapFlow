"""Central analysis modes and prompts."""
import json
from src.modes import DEBUG_SCHEMA, EXTRACT_SCHEMAS

MODES = {
    "ask": "Ask", "explain": "Explain", "debug": "Debug",
    "translate": "Translate", "extract": "Extract", "smart": "Smart",
}
ALIASES = {'general': 'ask', 'summarize': 'ask', 'ocr': 'extract'}
PROMPTS = {
    "ask": "Analyze the screenshot. Answer the user's question clearly. Use the conversation context for follow-up questions.",
    "explain": "Explain this screenshot clearly and step by step for a university student.",
    "debug": "Diagnose visible code or errors. Return only a JSON object matching this schema: " + json.dumps(DEBUG_SCHEMA) + ". Evidence must come from the screenshot. Distinguish hypotheses from visible facts. If no error can be identified, say so, use low confidence and empty evidence/fixes arrays. Do not invent missing code.",
    "translate": "Translate the visible text into English, preserving meaning and formatting where practical.",
    "extract": (
        "Classify and extract visible information. Return exactly one JSON object, without commentary or Markdown fences. "
        "Put content_type and all extracted fields at the top level. Do not nest fields under the type name or a data object. "
        "Choose one content_type from this field reference (the outer names identify types, not output wrappers): "
        + json.dumps(EXTRACT_SCHEMAS)
        + '. Example code output: ' + json.dumps({'content_type': 'code', 'language': 'python', 'code': 'x = 1\nprint(x)'})
        + '. Example table output: ' + json.dumps({'content_type': 'table', 'headers': ['Name', 'Score'], 'rows': [['Alice', 92]]})
        + '. Example text output: ' + json.dumps({'content_type': 'text', 'text': 'Hello'})
        + '. Preserve text, indentation and code verbatim. Encode line breaks using JSON newline escapes exactly once. '
        'Table rows must match the header count; use null for unreadable cells. Use null for missing nullable fields. '
        'Never invent dates, values or contact details. For unsupported content use content_type=text and transcribe readable text '
        '(empty string if none). For visible JSON use content_type=json and put the extracted JSON in value.'
    ),
}


def get_prompt(mode: str) -> str:
    try:
        return PROMPTS[ALIASES.get(mode, mode)]
    except KeyError:
        raise ValueError(f"Unknown analysis mode: {mode}") from None
