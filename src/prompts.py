"""Central analysis modes and prompts."""

MODES = {
    "general": "General", "explain": "Explain", "debug": "Debug Code",
    "translate": "Translate", "summarize": "Summarize", "ocr": "Extract Text",
}
PROMPTS = {
    "general": "Analyze the screenshot. Answer any question clearly, explain code or errors, and identify important information.",
    "explain": "Explain this screenshot clearly and step by step for a university student.",
    "debug": "Analyze the code or error. Identify the likely problem, why it happens, and how to fix it. Provide corrected code when useful.",
    "translate": "Translate the visible text into English, preserving meaning and formatting where practical.",
    "summarize": "Summarize the important information in this screenshot concisely.",
    "ocr": "Extract all readable text from the screenshot. Return only the extracted text.",
}


def get_prompt(mode: str) -> str:
    try:
        return PROMPTS[mode]
    except KeyError:
        raise ValueError(f"Unknown analysis mode: {mode}") from None
