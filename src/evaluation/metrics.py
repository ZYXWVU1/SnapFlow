"""Typed comparison and metrics with fixed denominators."""
from datetime import date, datetime, time
import math
import re


def present(value):
    return value is not None and value != '' and value != []


def _text(value):
    return ' '.join(value.split()).casefold() if isinstance(value, str) else None


def compare_value(kind, expected, actual):
    if not present(expected):
        return not present(actual)
    if not present(actual):
        return False
    if kind == 'number':
        return (type(expected) in (int, float) and type(actual) in (int, float)
                and math.isfinite(expected) and math.isfinite(actual)
                and math.isclose(expected, actual, rel_tol=0, abs_tol=1e-6))
    if kind == 'boolean':
        return type(expected) is bool and type(actual) is bool and expected == actual
    if kind == 'list_string':
        return (isinstance(expected, list) and isinstance(actual, list)
                and [_text(v) for v in expected] == [_text(v) for v in actual])
    if kind in ('date', 'time', 'datetime'):
        if not isinstance(expected, str) or not isinstance(actual, str):
            return False
        try:
            parser = {'date': date.fromisoformat, 'time': time.fromisoformat,
                      'datetime': datetime.fromisoformat}[kind]
            return parser(expected) == parser(actual)
        except ValueError:
            return False
    return _text(expected) == _text(actual)


def calculate_metrics(skill_id, fields, cases):
    counts = dict(classified=0, classification_correct=0, expected_present=0,
        present_correct=0, required_present=0, required_covered=0,
        expected_missing=0, missing_correct=0, unsupported=0, schema_valid=0,
        latency_count=0, latency_sum=0, usage_tokens=0)
    for case in cases:
        predicted = case.get('predicted_skill_id')
        if predicted is not None:
            counts['classified'] += 1
            counts['classification_correct'] += predicted == skill_id
        counts['schema_valid'] += bool(case.get('schema_valid'))
        latency = case.get('latency_ms')
        if type(latency) in (int, float) and latency >= 0 and math.isfinite(latency):
            counts['latency_count'] += 1
            counts['latency_sum'] += latency
        usage = case.get('usage_tokens')
        if type(usage) is int and usage >= 0:
            counts['usage_tokens'] += usage
        expected, actual = case.get('expected') or {}, case.get('actual') or {}
        for field_id, kind, required in fields:
            truth, output = expected.get(field_id), actual.get(field_id)
            if present(truth):
                counts['expected_present'] += 1
                counts['present_correct'] += compare_value(kind, truth, output)
                if required:
                    counts['required_present'] += 1
                    counts['required_covered'] += present(output)
            else:
                counts['expected_missing'] += 1
                counts['missing_correct'] += not present(output)
                counts['unsupported'] += present(output)

    def ratio(num, den):
        return num / den if den else None

    return {
        'classification_accuracy': ratio(counts['classification_correct'], counts['classified']),
        'field_accuracy': ratio(counts['present_correct'], counts['expected_present']),
        'required_field_coverage': ratio(counts['required_covered'], counts['required_present']),
        'missing_field_accuracy': ratio(counts['missing_correct'], counts['expected_missing']),
        'hallucination_rate': ratio(counts['unsupported'], counts['expected_missing']),
        'schema_validity_rate': ratio(counts['schema_valid'], len(cases)),
        'average_latency_ms': ratio(counts['latency_sum'], counts['latency_count']),
        'usage_tokens': counts['usage_tokens'] if any('usage_tokens' in c for c in cases) else None,
        'denominators': {key: counts[key] for key in ('classified', 'expected_present', 'required_present',
                                                     'expected_missing', 'latency_count')},
        'case_count': len(cases),
    }
