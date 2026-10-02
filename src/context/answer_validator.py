"""Reject response sentences with missing or unrecognized source references."""
import re


_REF = re.compile(r'\[((?:MCP|[A-Za-z])\d+)\]')
_MONEY = re.compile(r'[$€£]\s*(\d[\d,]*(?:\.\d+)?)')


def validate_answer(response, sources):
    allowed = {source.source_id for source in sources}
    by_id = {source.source_id: source for source in sources}
    kept = []
    used = set()
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', response.strip()):
        cited = set(_REF.findall(sentence))
        amounts = [amount.replace(',', '') for amount in _MONEY.findall(sentence)]
        supported_amounts = cited <= allowed and all(any(re.search(r'(?<!\d)' + re.escape(amount) + r'(?!\d)',
            by_id[source_id].content.replace(',', '')) for source_id in cited)
            for amount in amounts)
        if cited and supported_amounts:
            kept.append(sentence)
            used.update(cited)
    if not kept:
        return 'I could not verify an answer from the available sources.', ()
    return ' '.join(kept), tuple(source.source_id for source in sources if source.source_id in used)
