"""Charge every inference attempt, including runtime fallback, before dispatch."""
from contextlib import contextmanager


@contextmanager
def inference_budget(client, charge, *, fallback_calls=1):
    scope = getattr(client, 'inference_scope', None)
    if fallback_calls and callable(scope):
        with scope(charge):
            yield
    else:
        # Trusted adapters without the runtime hook conservatively reserve calls.
        for _ in range(fallback_calls):
            charge()
        yield
