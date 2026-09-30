"""Run labeled retrieval scenarios on a disposable Memory database."""
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from src.context.models import ContextRequest, ContextSession
from src.context.permissions import ContextPermissionService
from src.context.retrieval import ContextRetrievalService
from src.evaluation.contextual.datasets import synthetic_cases
from src.evaluation.contextual.metrics import retrieval_metrics
from src.memory.storage import MemoryStore
from src.paths import AppPaths
from src.skills.base import SkillResult


@dataclass(frozen=True)
class RetrievalEvaluation:
    name: str
    retrieved_ids: tuple[str, ...]
    metrics: dict


def run_synthetic_evaluation(cases=None):
    outcomes = []
    for case in cases if cases is not None else synthetic_cases():
        with TemporaryDirectory() as directory:
            store = MemoryStore(AppPaths(data_dir=Path(directory)))
            ids = {}
            for label, fields in case.historical_records:
                record = store.save_result(SkillResult('synthetic_context',
                    'Synthetic Context', 1.0, fields, []), title=label)
                ids[label] = record.id
            session = ContextSession.new('synthetic-current')
            permissions = ContextPermissionService()
            if case.scope == 'selected_memories':
                permissions.grant_selected(session,
                    tuple(ids[label] for label in case.authorized_ids))
            elif case.scope == 'authorized_memory_search':
                permissions.grant_search(session)
            elif case.scope != 'current_only':
                raise ValueError('Unknown synthetic context scope.')
            request = ContextRequest.new(session, case.question,
                current_skill_result=case.current_fields)
            started = perf_counter()
            records = ContextRetrievalService(store, permissions).retrieve(session, request)
            elapsed_ms = (perf_counter() - started) * 1000
            labels = {memory_id: label for label, memory_id in ids.items()}
            retrieved = tuple(labels[record.id] for record in records)
            metrics = retrieval_metrics(retrieved, relevant_ids=case.relevant_ids,
                authorized_ids=case.authorized_ids, k=8, latency_ms=elapsed_ms)
            outcomes.append(RetrievalEvaluation(case.name, retrieved, metrics))
    return tuple(outcomes)
