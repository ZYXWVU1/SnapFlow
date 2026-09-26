"""Typed, deterministic flat conditions. No expression evaluation."""
import math


def is_number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def evaluate_condition(condition, data):
    actual = data.get(condition.field)
    expected, op = condition.value, condition.operator
    empty = actual is None or actual == '' or actual == [] or actual == {}
    if op == 'exists':
        return actual is not None
    if op == 'not_exists':
        return actual is None
    if op == 'is_empty':
        return empty
    if op == 'is_not_empty':
        return not empty
    if op in ('greater_than', 'less_than'):
        if not is_number(actual) or not is_number(expected):
            return False
        return actual > expected if op == 'greater_than' else actual < expected
    if op in ('contains', 'not_contains'):
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        matches = expected.casefold() in actual.casefold()
        return matches if op == 'contains' else not matches
    if op in ('equals', 'not_equals'):
        if type(actual) is not type(expected) and not (is_number(actual) and is_number(expected)):
            return False
        matches = actual.casefold() == expected.casefold() if isinstance(actual, str) else actual == expected
        return matches if op == 'equals' else not matches
    return False


def evaluate_conditions(conditions, data):
    return all(evaluate_condition(c, data) for c in conditions)
