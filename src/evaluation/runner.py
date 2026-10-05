"""Synchronous evaluation core; UI calls it through a background QRunnable."""
from datetime import datetime, timezone
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import inspect
from pathlib import Path
import time
from uuid import uuid4

from src.skills.custom.models import CustomSkillDefinition
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.skills.custom.matcher import CustomSkillMatcher
from src.skills.custom.json_response import json_object
from src.skills.custom.validator import normalize_value
from src.evaluation.metrics import calculate_metrics, compare_value
from src.evaluation.storage import EvaluationReport


MAX_EVALUATION_IMAGE_BYTES = 32 * 1024 * 1024
MAX_EVALUATION_SNAPSHOT_BYTES = 256 * 1024 * 1024


def check_evaluation_cancelled(cancelled):
    if cancelled and cancelled():
        raise ValueError('AI evaluation cancelled.')


@dataclass(frozen=True)
class EvaluationSnapshot:
    version: object
    dataset: object
    cases: tuple


def freeze_evaluation(version_manager, dataset_storage, version_id, dataset_id, max_cases=None, *, cancelled=None):
    """Capture answers, Skill definition and screenshot bytes before model requests."""
    check_evaluation_cancelled(cancelled)
    version, dataset = version_manager.get(version_id), dataset_storage.get(dataset_id)
    if version is None or dataset is None or version.skill_id != dataset.skill_id:
        raise ValueError('Choose a Skill version and a dataset for the same Skill.')
    if max_cases is not None and (type(max_cases) is not int or max_cases < 1):
        raise ValueError('Evaluation case limit must be a positive integer.')
    examples = deepcopy(dataset_storage.available_examples(dataset))
    if max_cases is not None:
        examples = examples[:max_cases]
    cases, snapshot_bytes = [], 0
    for example in examples:
        check_evaluation_cancelled(cancelled)
        image, failure = None, None
        if not example.screenshot_reference:
            failure = 'no saved screenshot'
        else:
            try:
                remaining = MAX_EVALUATION_SNAPSHOT_BYTES - snapshot_bytes
                with Path(example.screenshot_reference).open('rb') as source:
                    image = source.read(min(MAX_EVALUATION_IMAGE_BYTES, remaining) + 1)
                if len(image) > remaining:
                    raise ValueError('Evaluation snapshot exceeds its memory limit. Reduce the case count.')
                if len(image) > MAX_EVALUATION_IMAGE_BYTES:
                    image, failure = None, 'screenshot exceeds AI image limit'
                else:
                    snapshot_bytes += len(image)
            except OSError:
                failure = 'screenshot unavailable'
        cases.append((example, image, failure))
    return EvaluationSnapshot(deepcopy(version), deepcopy(dataset), tuple(cases))


def _supports_keyword(client, name):
    parameters = inspect.signature(client.request_image).parameters
    return name in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD
                                     for parameter in parameters.values())


def valid_schema(definition, response):
    try:
        raw = json_object(response)
        if set(raw) != {field.id for field in definition.fields}:
            return False
        return all(value is None or normalize_value(field.field_type, value) is not None
                   for field in definition.fields for value in (raw[field.id],))
    except ValueError:
        return False


class EvaluationRunner:
    def __init__(self, feedback_storage, dataset_storage, version_manager, report_storage, client):
        self.feedback_storage = feedback_storage
        self.dataset_storage = dataset_storage
        self.version_manager = version_manager
        self.report_storage = report_storage
        self.client = client

    def evaluate(self, version_id, dataset_id, model_config_id, max_cases=None, *,
                 snapshot=None, runtime_metadata=None, runtime_validation=False, cancelled=None):
        check_evaluation_cancelled(cancelled)
        snapshot = snapshot or freeze_evaluation(self.version_manager, self.dataset_storage,
                                                 version_id, dataset_id, max_cases, cancelled=cancelled)
        version, dataset = snapshot.version, snapshot.dataset
        if version.version_id != version_id or dataset.id != dataset_id or version.skill_id != dataset.skill_id:
            raise ValueError('Choose a Skill version and a dataset for the same Skill.')
        definition = CustomSkillDefinition.from_dict(version.definition_snapshot)
        skill = RuntimeCustomSkill(definition)
        fields = [(f.id, f.field_type, f.required) for f in definition.fields]
        usage_options = ({'model_override': model_config_id, 'usage_operation': 'evaluation'}
                         if hasattr(self.client, 'usage_callback') else {})
        metric_cases, case_results, failures = [], [], []
        failed_cases = 0
        for example, image, failure in snapshot.cases:
            check_evaluation_cancelled(cancelled)
            if failure:
                failures.append(f'{example.id}: {failure}')
                continue
            start = time.perf_counter()
            prediction = CustomSkillMatcher(self.client, **usage_options).match(image, [definition])
            check_evaluation_cancelled(cancelled)
            case_failed = prediction.failed
            actual, valid, errors = {}, False, []
            try:
                request_options = dict(usage_options)
                if runtime_validation and _supports_keyword(self.client, 'validator'):
                    def validate(response):
                        if not valid_schema(definition, response):
                            raise ValueError('Evaluation output does not match the existing Skill schema.')
                    request_options['validator'] = validate
                response = self.client.request_image(image, skill.prompt(), mode='skill', **request_options)
                check_evaluation_cancelled(cancelled)
                extracted = skill.parse(response, prediction.confidence)
                actual = extracted.data
                errors = list(extracted.warnings)
                valid = valid_schema(definition, response)
            except Exception as exc:
                check_evaluation_cancelled(cancelled)
                errors = [type(exc).__name__ + ': extraction failed']
                case_failed = True
            failed_cases += case_failed
            latency = round((time.perf_counter() - start) * 1000, 3)
            metric_cases.append(dict(expected=example.corrected_result, actual=actual,
                schema_valid=valid, predicted_skill_id=prediction.skill_id or 'unknown',
                latency_ms=latency))
            incorrect = [field_id for field_id, kind, _ in fields
                         if not compare_value(kind, example.corrected_result.get(field_id), actual.get(field_id))]
            details = dict(example_id=example.id, expected=example.corrected_result,
                actual=actual, incorrect_fields=incorrect, validation_errors=errors,
                skill_version_id=version_id, model_config_id=model_config_id,
                latency_ms=latency, schema_valid=valid)
            if runtime_metadata:
                details['runtime_metadata'] = dict(runtime_metadata)
            case_results.append(details)
        check_evaluation_cancelled(cancelled)
        metrics = calculate_metrics(definition.id, fields, metric_cases)
        total_cases = len(snapshot.cases)
        metrics['failed_case_count'] = failed_cases + len(failures)
        metrics['failure_rate'] = (failed_cases + len(failures)) / total_cases if total_cases else None
        prompt_hash = hashlib.sha256((definition.detection_prompt + '\n' + skill.prompt()).encode('utf-8')).hexdigest()
        report = EvaluationReport(uuid4().hex, version.skill_id, version_id, dataset.id,
            dataset.revision, str(model_config_id), prompt_hash,
            datetime.now(timezone.utc).isoformat(), total_cases, metrics, failures)
        check_evaluation_cancelled(cancelled)
        self.report_storage.save(report, case_results)
        return report
