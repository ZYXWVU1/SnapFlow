"""Deterministic retrieval and citation measurements for labeled scenarios."""
import math
import re


_CITATION = re.compile(r'\[([A-Za-z]\d+)\]')


def retrieval_metrics(retrieved_ids, *, relevant_ids, authorized_ids, k, latency_ms=None):
    if type(k) is not int or k < 1:
        raise ValueError('k must be a positive integer.')
    ranked = tuple(retrieved_ids)[:k]
    relevant = set(relevant_ids)
    authorized = set(authorized_ids)
    hits = len(set(ranked) & relevant)
    latency = (float(latency_ms) if type(latency_ms) in (int, float)
               and math.isfinite(latency_ms) and latency_ms >= 0 else None)
    return {
        'precision_at_k': hits / k,
        'recall_at_k': hits / len(relevant) if relevant else None,
        'correct_source_selection': set(ranked) & relevant == relevant if relevant else None,
        'unauthorized_retrieval_count': sum(memory_id not in authorized for memory_id in ranked),
        'search_latency_ms': latency,
    }


def citation_metrics(answer, *, available_ids):
    cited = _CITATION.findall(answer)
    valid = sum(source_id in set(available_ids) for source_id in cited)
    return {
        'citation_count': len(cited),
        'valid_citation_count': valid,
        'citation_validity': valid / len(cited) if cited else None,
    }
