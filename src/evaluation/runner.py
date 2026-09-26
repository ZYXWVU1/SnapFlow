"""Synchronous evaluation core; UI calls it through a background QRunnable."""
from datetime import datetime, timezone
import hashlib
import time
from uuid import uuid4

from src.skills.custom.models import CustomSkillDefinition
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.skills.custom.matcher import CustomSkillMatcher
from src.skills.custom.json_response import json_object
from src.skills.custom.validator import normalize_value
from src.evaluation.metrics import calculate_metrics, compare_value
from src.evaluation.storage import EvaluationReport


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

    def evaluate(self, version_id, dataset_id, model_config_id):
        version = self.version_manager.get(version_id)
        dataset = self.dataset_storage.get(dataset_id)
        if version is None or dataset is None or version.skill_id != dataset.skill_id:
            raise ValueError('Choose a Skill version and a dataset for the same Skill.')
        definition = CustomSkillDefinition.from_dict(version.definition_snapshot)
        skill = RuntimeCustomSkill(definition)
        fields = [(f.id, f.field_type, f.required) for f in definition.fields]
        metric_cases, case_results, failures = [], [], []
        for example in self.dataset_storage.available_examples(dataset):
            if not example.screenshot_reference:
                failures.append(f'{example.id}: no saved screenshot')
                continue
            try:
                image = __import__('pathlib').Path(example.screenshot_reference).read_bytes()
            except OSError:
                failures.append(f'{example.id}: screenshot unavailable')
                continue
            start = time.perf_counter()
            prediction = CustomSkillMatcher(self.client).match(image, [definition])
            actual, valid, errors = {}, False, []
            try:
                response = self.client.request_image(image, skill.prompt(), mode='skill')
                extracted = skill.parse(response, prediction.confidence)
                actual = extracted.data
                errors = list(extracted.warnings)
                valid = valid_schema(definition, response)
            except Exception as exc:
                errors = [type(exc).__name__ + ': extraction failed']
            latency = round((time.perf_counter() - start) * 1000, 3)
            metric_cases.append(dict(expected=example.corrected_result, actual=actual,
                schema_valid=valid, predicted_skill_id=prediction.skill_id or 'unknown',
                latency_ms=latency))
            incorrect = [field_id for field_id, kind, _ in fields
                         if not compare_value(kind, example.corrected_result.get(field_id), actual.get(field_id))]
            case_results.append(dict(example_id=example.id, expected=example.corrected_result,
                actual=actual, incorrect_fields=incorrect, validation_errors=errors,
                skill_version_id=version_id, model_config_id=model_config_id,
                latency_ms=latency, schema_valid=valid))
        metrics = calculate_metrics(definition.id, fields, metric_cases)
        prompt_hash = hashlib.sha256((definition.detection_prompt + '\n' + skill.prompt()).encode('utf-8')).hexdigest()
        report = EvaluationReport(uuid4().hex, version.skill_id, version_id, dataset.id,
            dataset.revision, str(model_config_id), prompt_hash,
            datetime.now(timezone.utc).isoformat(), len(dataset.example_ids), metrics, failures)
        self.report_storage.save(report, case_results)
        return report
