"""Deterministic presentation of Ask prerequisites; never grants authority."""
from dataclasses import dataclass


@dataclass(frozen=True)
class AskState:
    ready: bool
    reason: str
    message: str


def ask_state(*, connected, authorized, available, fresh, question, busy, readable=True):
    if not connected:
        return AskState(False, 'disconnected', 'Connect the browser extension to SnapFlow first.')
    if not authorized:
        return AskState(False, 'not_authorized', 'Click Assist This Tab in the extension to share this page.')
    if not fresh:
        return AskState(False, 'expired_context', 'Page context expired. Click Refresh Page Context in the extension.')
    if not available:
        return AskState(False, 'capturing', 'No page context received yet. Use Refresh Page Context in the extension.')
    if not readable:
        return AskState(False, 'no_readable_context', 'No readable page content was captured. Refresh or assist another page.')
    if busy:
        return AskState(False, 'answering', 'Answering from the reviewed source. You can cancel this request.')
    if not question.strip():
        return AskState(False, 'question_required', 'Enter a question below to enable Ask.')
    if len(question) > 500:
        return AskState(False, 'question_required', 'Keep your question within 500 characters.')
    return AskState(True, 'ready', 'Ready. Ask sends only the reviewed source using the AI routing shown here.')
