"""Sentence boundaries that keep trailing source labels with their claim."""
import re


_LABEL = r'\[(?:MCP|[A-Za-z])\d+\]'
_REFERENCE = re.compile(_LABEL)
_LEADING_REFERENCES = re.compile(
    rf'^{_LABEL}(?:(?:[ \t]*[,;]?[ \t]*|[ \t]+and[ \t]+){_LABEL})*[ \t]*')


def cited_sentences(response):
    sentences = []
    for fragment in re.split(r'(?<=[.!?])\s+|\n+', response.strip()):
        fragment = fragment.strip()
        if (sentences and _REFERENCE.search(fragment) and
                not re.search(r'\w', _REFERENCE.sub('', fragment))):
            # Preserve punctuation and every label in standalone citation groups,
            # including unknown labels that the caller must still reject.
            sentences[-1] += ' ' + fragment
            continue
        leading = _LEADING_REFERENCES.match(fragment)
        if leading and sentences:
            previous_claim = _REFERENCE.sub('', sentences[-1]).rstrip()
            if previous_claim.endswith(('.', '!', '?')):
                sentences[-1] += ' ' + leading.group().strip()
                fragment = fragment[leading.end():].strip()
        if fragment:
            sentences.append(fragment)
    # A label alone is not an answer and must not count as verified evidence.
    return [sentence for sentence in sentences
            if re.search(r'\w', _REFERENCE.sub('', sentence))]
