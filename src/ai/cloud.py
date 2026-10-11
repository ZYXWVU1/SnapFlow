"""Adapt the established cloud transport without duplicating provider-specific behavior."""
import time
import json
from dataclasses import replace
from .models import AIResponse, ModelCapabilities
from .validation import schema_validator, validate_schema


class CloudRuntime:
    runtime_id = 'configured_cloud'
    local = False
    capabilities = ModelCapabilities(text=True, vision=True, structured_output=True)

    def __init__(self, model_id, executor, endpoint, policy):
        self.model_id, self.executor = model_id, executor
        self.endpoint, self.policy = endpoint, policy

    def health_check(self):
        # Connectivity is established by dispatch, not an extra paid probe.
        return True

    def list_models(self):
        return [self.model_id]

    def get_capabilities(self):
        return self.capabilities

    def shutdown(self):
        pass

    def generate_text(self, request):
        if request.privacy_requirement in ('local_only', 'private'):
            raise ValueError('This request forbids cloud AI.')
        self.policy.require_allowed(self.endpoint, 'cloud_ai')
        request = replace(request, preferred_model=self.model_id)
        if request.response_schema:
            schema_validator(request.response_schema)
            request = replace(request, prompt=request.prompt + '\nReturn JSON matching this schema:\n' + json.dumps(request.response_schema))
        started = time.perf_counter()
        image_metadata = None
        if request.image_bytes:
            from .images import prepare_image
            payload, image_metadata = prepare_image(request.image_bytes, request.metadata.get('cloud_max_image_width'))
            request = replace(request, image_bytes=payload, metadata={**request.metadata, 'image_encoding': image_metadata['encoding']})
        from .cancellation import run_cancellable
        content = run_cancellable(lambda: self.executor(request), request.cancelled, request.timeout_seconds)
        structured = validate_schema(request.response_schema, content) if request.response_schema else None
        return AIResponse(request.request_id, self.runtime_id, self.model_id, content,
                          structured_data=structured, latency_ms=(time.perf_counter() - started) * 1000, local=False,
                          image_metadata=image_metadata)

    analyze_image = generate_text
