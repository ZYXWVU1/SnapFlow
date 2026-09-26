"""A non-secret identifier for the configured evaluation provider and model."""
import os
from urllib.parse import urlsplit


def current_model_identifier():
    base_url = os.getenv('AI_BASE_URL') or 'https://api.openai.com/v1'
    host = urlsplit(base_url).hostname or 'configured-provider'
    model = os.getenv('AI_MODEL') or 'gpt-4.1-mini'
    return f'{host}/{model}'
