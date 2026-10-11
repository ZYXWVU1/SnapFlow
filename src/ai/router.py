"""Deterministic policy routing; provider-independent feature validation stays above it."""
from .models import RoutingDecision
from src.observability.performance import trace, measured, emit


class AIRuntimeRouter:
    def __init__(self, runtimes, config, policy, *, performance_store=None, fallback_callback=None, config_provider=None):
        self.runtimes, self.config, self.policy = tuple(runtimes), config, policy
        self.last_decision = None
        self.performance_store = performance_store
        self.fallback_callback = fallback_callback
        self.config_provider = config_provider or (lambda: self.config)

    def _compatible(self, runtime, request):
        caps = runtime.capabilities
        if request.image_bytes and not caps.vision:
            return False
        if not request.image_bytes and not caps.text:
            return False
        if request.response_schema and not caps.structured_output:
            return False
        if request.preferred_model and runtime.model_id != request.preferred_model:
            return False
        available = request.metadata.get('available_ram_mb')
        expected = getattr(runtime, 'expected_ram_mb', None)
        if runtime.local and expected is not None and available is not None and expected > available * 0.8:
            return False
        # UTF-8 byte length is a conservative token upper bound for text.
        context = len(request.prompt.encode('utf-8')) + sum(len(str(item).encode('utf-8')) for item in request.conversation)
        context += sum(len(str(request.metadata.get(name) or '').encode('utf-8')) for name in ('system_prompt', 'custom_prompt'))
        if request.response_schema:
            import json
            context += len(json.dumps(request.response_schema).encode('utf-8'))
        if caps.max_context_length and context + request.metadata.get('max_tokens', 1024) > caps.max_context_length:
            return False
        return True

    def _profile(self, runtime, request):
        if self.performance_store is None:
            return None
        data = request.metadata
        return self.performance_store.eligible(runtime.runtime_id, runtime.model_id,
            data.get('skill_id'), data.get('dataset_revision'),
            data.get('model_versions', {}).get(runtime.runtime_id, data.get('model_version')),
            data.get('hardware_profile_id'), skill_version_id=data.get('skill_version_id'),
            dataset_id=data.get('dataset_id'))

    def _check_cancel(self, request):
        if request.cancelled and request.cancelled():
            raise ValueError('AI request cancelled.')

    def _check_external(self, request, mode):
        if mode in ('automatic', 'prefer_local'):
            from src.reliability.policy import get_safety_policy
            get_safety_policy().require('cloud_fallback')
        active = self.config_provider()
        if self.policy.private_mode or active.private_mode or active.ai_execution_mode == 'local_only' or request.privacy_requirement in ('private', 'local_only'):
            raise ValueError('Cloud execution is forbidden by the current privacy policy.')
        if mode in ('automatic', 'prefer_local') and active.ai_execution_mode != 'cloud_only' and not active.allow_cloud_fallback:
            raise ValueError('Cloud fallback is disabled by the current policy.')

    @measured('ai_request', 'ai_router')
    def execute(self, request, validator=None):
        self._check_cancel(request)
        private = self.policy.private_mode or self.config.private_mode or request.privacy_requirement == 'private'
        local_only = private or self.config.ai_execution_mode == 'local_only' or request.privacy_requirement == 'local_only'
        mode = 'local_only' if local_only else self.config.ai_execution_mode
        if mode == 'ask_every_time':
            mode = request.metadata.get('runtime_choice')
            if mode not in ('local_only', 'cloud_only'):
                raise ValueError('Choose Local or Cloud for this request, or cancel.')
        with trace('ai_selection', 'ai_router'):
            candidates = [runtime for runtime in self.runtimes if self._compatible(runtime, request)]
        if mode == 'local_only':
            candidates = [runtime for runtime in candidates if runtime.local]
        elif mode == 'cloud_only':
            candidates = [runtime for runtime in candidates if not runtime.local]
        else:
            candidates.sort(key=lambda runtime: not runtime.local)
            if not self.config.allow_cloud_fallback:
                candidates = [runtime for runtime in candidates if runtime.local]
        if not candidates:
            raise ValueError('No compatible AI model is available for this policy, capability or context size. Open Settings → AI & Models.')
        quality_reason = None
        if mode == 'automatic' and self.config.allow_cloud_fallback and len(candidates) > 1:
            local = next((candidate for candidate in candidates if candidate.local), None)
            cloud = next((candidate for candidate in candidates if not candidate.local), None)
            local_profile = self._profile(local, request) if local else None
            cloud_profile = self._profile(cloud, request) if cloud else None
            if local_profile and cloud_profile and local_profile.field_accuracy is not None and cloud_profile.field_accuracy is not None:
                if local_profile.field_accuracy + getattr(self.config, 'local_quality_tolerance', 0.05) < cloud_profile.field_accuracy:
                    candidates = [cloud]
                    quality_reason = 'QUALITY_THRESHOLD'
        failed_local = False
        last_error = None
        for runtime in candidates:
            self._check_cancel(request)
            # Re-evaluate privacy before every attempt and after failed validation.
            now_private = self.policy.private_mode or self.config.private_mode or request.privacy_requirement == 'private'
            if not runtime.local:
                self._check_external(request, mode)
                if now_private or local_only:
                    raise ValueError('Cloud execution is forbidden by the current privacy policy.')
                self.policy.require_allowed(getattr(runtime, 'endpoint', 'https://cloud.invalid'), 'cloud_ai')
            reasons = ['PRIVATE_MODE' if now_private else 'LOCAL_ONLY' if mode == 'local_only' else
                       'USER_SELECTED' if mode == 'cloud_only' else
                       'LOCAL_MODEL_AVAILABLE' if runtime.local else 'CLOUD_FALLBACK_ALLOWED']
            if request.image_bytes:
                reasons.append('VISION_REQUIRED')
            if quality_reason:
                reasons.append(quality_reason)
            if failed_local:
                reasons.append('LOCAL_MODEL_FAILED')
            profile = self._profile(runtime, request)
            allowed = not local_only and mode in ('automatic', 'prefer_local') and self.config.allow_cloud_fallback
            self.last_decision = RoutingDecision(request.request_id, runtime.runtime_id, runtime.model_id,
                tuple(reasons), runtime.local, allowed, estimated_quality=profile.field_accuracy if profile else None,
                estimated_latency_ms=profile.avg_latency_ms if profile else None,
                estimated_cost=0.0 if runtime.local else profile.estimated_cost_per_request if profile else None)
            if not runtime.local and mode in ('automatic', 'prefer_local') and self.fallback_callback:
                self.fallback_callback(self.last_decision)
            try:
                from .cancellation import run_cancellable
                with trace('ai_health', 'ai_router'):
                    if not run_cancellable(runtime.health_check, request.cancelled, min(request.timeout_seconds, 10)):
                        raise ValueError('The selected runtime is offline or its model is not loaded.')
                self._check_cancel(request)
                if not runtime.local:
                    self._check_external(request, mode)
                if request.before_inference:
                    request.before_inference()
                with trace('vision_inference', 'ai_router'):
                    response = runtime.analyze_image(request) if request.image_bytes else runtime.generate_text(request)
                with trace('response_validation', 'ai_router'):
                    if not isinstance(response.content, str) or not response.content.strip():
                        raise ValueError('The runtime returned an empty response.')
                    if validator:
                        validator(response.content)
                self._check_cancel(request)
                emit('ai_request_completed', component='ai_router', success=True,
                     duration_ms=response.latency_ms if hasattr(response, 'latency_ms') else None,
                     properties={'runtime': 'local' if runtime.local else 'cloud', 'fallback': failed_local})
                return response
            except Exception as exc:
                emit('ai_request_completed', component='ai_router', success=False,
                     properties={'runtime': 'local' if runtime.local else 'cloud', 'fallback': failed_local})
                if not runtime.local or not allowed:
                    raise
                failed_local = True
                last_error = exc
        raise ValueError('No allowed runtime could complete the request. Check Local AI health in Settings.') from last_error
