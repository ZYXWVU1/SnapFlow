"""Explicit local/cloud comparison through the existing frozen Skill evaluator."""
from dataclasses import dataclass
import inspect
import math
import os

from src.evaluation.runner import EvaluationRunner, freeze_evaluation, check_evaluation_cancelled
from src.network_policy import get_network_policy
from .performance import ModelPerformanceProfile


@dataclass(frozen=True)
class ModelComparisonResult:
    local_report: object
    cloud_report: object
    local_profile: ModelPerformanceProfile
    cloud_profile: ModelPerformanceProfile
    case_count: int
    potential_cloud_requests: int


class _SelectedModelClient:
    """Pin the selected identity and reject an unintended inference provenance."""
    def __init__(self, client, *, runtime_id, model_id, local, policy, cancelled=None):
        self.client, self.runtime_id, self.model_id = client, runtime_id, model_id
        self.local, self.policy = local, policy
        self.usage_callback = getattr(client, 'usage_callback', None)
        self.request_count = 0
        self.cancelled = cancelled

    def request_image(self, image, prompt, mode='', **options):
        check_evaluation_cancelled(self.cancelled)
        config = getattr(self.client, 'config', None)
        destination = (getattr(config, 'local_ai_endpoint', 'http://127.0.0.1:8080/v1') if self.local else
                       os.getenv('AI_BASE_URL') or 'https://api.openai.com/v1')
        self.policy.require_allowed(destination, 'local_ai' if self.local else 'cloud_ai')
        parameters = inspect.signature(self.client.request_image).parameters
        accepts_options = any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values())
        options.update(model_override=self.model_id, usage_operation='evaluation')
        optional = {'runtime_choice': 'local_only' if self.local else 'cloud_only'}
        if self.cancelled is not None:
            optional['cancelled'] = self.cancelled
        if self.local:
            optional['privacy_requirement'] = 'local_only'
        options.update({key: value for key, value in optional.items() if accepts_options or key in parameters})
        self.request_count += 1
        content = self.client.request_image(image, prompt, mode=mode, **options)
        check_evaluation_cancelled(self.cancelled)
        if hasattr(self.client, 'last_response'):
            response = self.client.last_response
            if (response is None or response.local != self.local or response.runtime_id != self.runtime_id or
                    response.model_id != self.model_id):
                raise ValueError('Evaluation response did not come from the selected model/runtime.')
        return content


class ModelComparisonService:
    def __init__(self, feedback_storage, dataset_storage, version_manager, report_storage,
                 performance_store, policy=None):
        self.feedback_storage, self.dataset_storage = feedback_storage, dataset_storage
        self.version_manager, self.report_storage = version_manager, report_storage
        self.performance_store = performance_store
        self.policy = policy or get_network_policy()

    def compare(self, local_client, cloud_client, version_id, dataset_id, *, local_model_id,
                cloud_model_id, local_model_version, cloud_model_version, hardware_profile_id,
                confirmed=False, max_cases=None, local_runtime_id='local_openai',
                cloud_runtime_id='configured_cloud', cancelled=None):
        check_evaluation_cancelled(cancelled)
        if confirmed is not True:
            raise ValueError('Explicitly confirm the dataset, models and potential cloud cost before comparison.')
        for value in (local_model_id, cloud_model_id, local_model_version, cloud_model_version,
                      hardware_profile_id, local_runtime_id, cloud_runtime_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError('Choose explicit model versions and a local hardware profile before comparison.')
        for client, mode, label in ((local_client, 'local_only', 'Local Only'),
                                   (cloud_client, 'cloud_only', 'Cloud Only')):
            config = getattr(client, 'config', None)
            if config is not None and config.ai_execution_mode != mode:
                raise ValueError('Comparison clients must use ' + label + ' for the selected runtime.')
        # Check before even local benchmark work; comparison always includes cloud.
        self.policy.require_allowed(os.getenv('AI_BASE_URL') or 'https://api.openai.com/v1', 'cloud_ai')
        snapshot = freeze_evaluation(self.version_manager, self.dataset_storage, version_id, dataset_id, max_cases,
                                     cancelled=cancelled)
        if not snapshot.cases:
            raise ValueError('Choose a dataset with available verified examples.')
        results, profiles = [], []
        for client, runtime_id, model_id, model_version, local in (
                (local_client, local_runtime_id, local_model_id, local_model_version, True),
                (cloud_client, cloud_runtime_id, cloud_model_id, cloud_model_version, False)):
            check_evaluation_cancelled(cancelled)
            if not local:
                self.policy.require_allowed(os.getenv('AI_BASE_URL') or 'https://api.openai.com/v1', 'cloud_ai')
            selected = _SelectedModelClient(client, runtime_id=runtime_id, model_id=model_id,
                                            local=local, policy=self.policy, cancelled=cancelled)
            runner = EvaluationRunner(self.feedback_storage, self.dataset_storage, self.version_manager,
                                      self.report_storage, selected)
            events = []
            has_callback = hasattr(client, 'usage_callback')
            original_callback = getattr(client, 'usage_callback', None)
            def collect(event):
                events.append(event)
                if original_callback is not None:
                    original_callback(event)
            if has_callback:
                client.usage_callback = collect
            try:
                report = runner.evaluate(version_id, dataset_id, model_id, snapshot=snapshot,
                    runtime_validation=True, cancelled=cancelled, runtime_metadata=dict(runtime_id=runtime_id, model_id=model_id,
                        model_version=model_version, local=local,
                        hardware_profile_id=hardware_profile_id if local else None))
            finally:
                if has_callback:
                    client.usage_callback = original_callback
            cost = self._measured_cost(events, model_id, selected.request_count)
            profile = ModelPerformanceProfile.from_report(report, runtime_id=runtime_id, model_id=model_id,
                model_version=model_version, local=local, hardware_profile_id=hardware_profile_id,
                estimated_cost_per_request=cost)
            results.append(report)
            profiles.append(profile)
        # A completed local report alone must not become comparison routing
        # evidence if the user cancels before the cloud side completes.
        check_evaluation_cancelled(cancelled)
        for profile in profiles:
            self.performance_store.save(profile)
        return ModelComparisonResult(results[0], results[1], profiles[0], profiles[1],
                                     len(snapshot.cases), len(snapshot.cases) * 2)

    @staticmethod
    def _measured_cost(events, model_id, request_count):
        # Partial or absent usage is unknown, rather than a zero cost estimate.
        if not request_count or len(events) != request_count:
            return None
        costs = []
        for event in events:
            cost = getattr(event, 'estimated_cost_usd', None)
            if (getattr(event, 'model', None) != model_id or getattr(event, 'operation', None) != 'evaluation' or
                    not getattr(event, 'usage_available', False) or type(cost) not in (float, int) or
                    not math.isfinite(cost) or cost < 0):
                return None
            costs.append(cost)
        return sum(costs) / request_count
