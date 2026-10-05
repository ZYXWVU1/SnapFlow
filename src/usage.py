"""Token-only API usage ledger and conservative cost estimates."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from urllib.parse import urlparse
import uuid

from src.database import initialize_learning_database


# OpenAI published rates per million tokens for gpt-4.1-mini. Verified 2026-09-26:
# https://developers.openai.com/api/docs/models/gpt-4.1-mini
_GPT_41_MINI_RATE = (0.40, 0.10, 1.60)  # uncached input, cached input, output


def normalize_provider(provider):
    value = str(provider or "").strip()
    try:
        parsed = urlparse(value if "://" in value else "https://" + value)
    except ValueError:
        return value.lower()
    return (parsed.hostname or value).lower()


def estimate_cost(provider, model, input_tokens, output_tokens, cached_input_tokens=0):
    """Return a USD estimate only for a model/provider with a known rate."""
    host = normalize_provider(provider)
    model_name = str(model or "").strip().lower()
    if host != "api.openai.com" or not model_name.startswith("gpt-4.1-mini"):
        return None
    values = (input_tokens, output_tokens, cached_input_tokens)
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("Token counts must be non-negative integers.")
    if cached_input_tokens > input_tokens:
        raise ValueError("Cached input tokens cannot exceed total input tokens.")
    input_rate, cached_rate, output_rate = _GPT_41_MINI_RATE
    input_cost = (input_tokens - cached_input_tokens) * input_rate
    cached_cost = cached_input_tokens * cached_rate
    output_cost = output_tokens * output_rate
    return (input_cost + cached_cost + output_cost) / 1_000_000


@dataclass(frozen=True)
class UsageEvent:
    provider: str
    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    latency_ms: int = 0
    succeeded: bool = True
    failure_category: str | None = None
    usage_available: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    local: bool = False
    estimated_cost_usd: float | None = field(init=False)

    def __post_init__(self):
        provider = normalize_provider(self.provider)
        model, operation = str(self.model).strip(), str(self.operation).strip()
        if not provider or not model or not operation or len(model) > 128 or len(operation) > 64:
            raise ValueError("Usage metadata is invalid.")
        if any(type(value) is not int or value < 0 for value in
               (self.input_tokens, self.output_tokens, self.cached_input_tokens, self.latency_ms)):
            raise ValueError("Usage counts and latency must be non-negative integers.")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed total input tokens.")
        if type(self.succeeded) is not bool or type(self.usage_available) is not bool:
            raise ValueError("Usage request state is invalid.")
        if self.failure_category is not None and self.failure_category not in (
                "timeout", "connection", "http_400", "http_401", "http_403", "http_404",
                "http_429", "http_other", "request_error"):
            raise ValueError("Usage failure category is invalid.")
        try:
            parsed_timestamp = datetime.fromisoformat(self.timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError("Usage timestamp is invalid.") from exc
        if parsed_timestamp.tzinfo is None:
            raise ValueError("Usage timestamp must include a timezone.")
        timestamp = parsed_timestamp.astimezone(timezone.utc).isoformat()
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "timestamp", timestamp)
        if type(self.local) is not bool:
            raise ValueError('Local usage state must be true or false.')
        object.__setattr__(self, "estimated_cost_usd", 0.0 if self.local else estimate_cost(
            provider, model, self.input_tokens, self.output_tokens, self.cached_input_tokens)
            if self.usage_available else None)

    @classmethod
    def create(cls, provider, model, operation, input_tokens, output_tokens,
               cached_input_tokens=0, **metadata):
        return cls(provider, model, operation, input_tokens, output_tokens,
                   cached_input_tokens, **metadata)


@dataclass(frozen=True)
class UsageSummary:
    month: str
    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    estimated_cost_usd: float | None = 0.0
    unknown_cost_calls: int = 0
    today_request_count: int = 0
    session_request_count: int = 0
    provider_model_breakdown: tuple = ()
    recent_failure_count: int = 0
    recent_failure_categories: tuple = ()


@dataclass(frozen=True)
class EvaluationCostPreview:
    case_count: int
    request_count: int
    estimated_cost_usd: float | None
    sample_count: int


class UsageStorage:
    """Persist token totals and safe request metadata; never request content."""

    def __init__(self, path):
        self.path = Path(path)

    def _prepare(self):
        initialize_learning_database(self.path)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def record(self, event):
        if not isinstance(event, UsageEvent):
            raise TypeError("Expected a UsageEvent.")
        self._prepare()
        connection = self._connect()
        try:
            with connection:
                connection.execute("""INSERT INTO api_usage
                    (id, timestamp, provider, model, operation, input_tokens, output_tokens,
                     cached_input_tokens, estimated_cost_usd, latency_ms, succeeded,
                     usage_available, failure_category)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event.id, event.timestamp, event.provider, event.model, event.operation,
                     event.input_tokens, event.output_tokens, event.cached_input_tokens,
                     event.estimated_cost_usd, event.latency_ms, int(event.succeeded),
                     int(event.usage_available), event.failure_category))
        finally:
            connection.close()
        return event

    def monthly_summary(self, now=None, session_start=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        month = now.strftime("%Y-%m")
        if not self.path.exists():
            return UsageSummary(month)
        self._prepare()
        connection = self._connect()
        try:
            row = connection.execute("""SELECT COUNT(*), COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cached_input_tokens), 0),
                SUM(estimated_cost_usd), SUM(CASE WHEN estimated_cost_usd IS NULL THEN 1 ELSE 0 END)
                FROM api_usage WHERE substr(timestamp, 1, 7) = ?""", (month,)).fetchone()
            breakdown = connection.execute("""SELECT provider, model, COUNT(*) FROM api_usage
                WHERE substr(timestamp, 1, 7) = ? GROUP BY provider, model
                ORDER BY COUNT(*) DESC, provider, model LIMIT 8""", (month,)).fetchall()
            day_start = now.strftime("%Y-%m-%d") + "T00:00:00"
            today_count = connection.execute(
                "SELECT COUNT(*) FROM api_usage WHERE timestamp >= ? AND timestamp <= ?",
                (day_start, now.isoformat())).fetchone()[0]
            session_count = 0
            if session_start:
                start = datetime.fromisoformat(session_start) if isinstance(session_start, str) else session_start
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                session_count = connection.execute(
                    "SELECT COUNT(*) FROM api_usage WHERE timestamp >= ? AND timestamp <= ?",
                    (start.astimezone(timezone.utc).isoformat(), now.isoformat())).fetchone()[0]
            failures = connection.execute("""SELECT failure_category FROM api_usage
                WHERE succeeded=0 AND timestamp >= ? ORDER BY timestamp DESC LIMIT 10""",
                (day_start,)).fetchall()
        finally:
            connection.close()
        count, inputs, outputs, cached, cost, unknown = row
        categories = tuple((category or "request_error") for (category,) in failures)
        return UsageSummary(month, count, inputs, outputs, cached,
                            None if unknown else (cost or 0.0), unknown or 0,
                            today_count, session_count, tuple(breakdown), len(categories), categories)

    def claim_monthly_warning(self, month):
        """Atomically return true once for each UTC month that crosses threshold."""
        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
        except (TypeError, ValueError) as exc:
            raise ValueError("Month must use YYYY-MM format.") from exc
        if parsed_month.strftime("%Y-%m") != month:
            raise ValueError("Month must use YYYY-MM format.")
        self._prepare()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM api_usage_state WHERE key='last_warning_month'").fetchone()
            if row and row[0] == month:
                connection.rollback()
                return False
            connection.execute("INSERT OR REPLACE INTO api_usage_state (key, value) VALUES ('last_warning_month', ?)",
                               (month,))
            connection.commit()
            return True
        finally:
            connection.close()

    def evaluation_preview(self, case_count, provider, model, *, comparisons=1):
        if type(case_count) is not int or case_count < 0 or type(comparisons) is not int or comparisons < 1:
            raise ValueError("Evaluation case and comparison counts are invalid.")
        request_count = case_count * 2 * comparisons
        if not request_count or not self.path.exists():
            return EvaluationCostPreview(case_count, request_count, None, 0)
        self._prepare()
        host = normalize_provider(provider)
        connection = self._connect()
        try:
            rows = connection.execute("""SELECT estimated_cost_usd FROM api_usage
                WHERE operation='evaluation' AND provider=? AND model=? AND usage_available=1
                ORDER BY timestamp DESC LIMIT 20""", (host, model.strip())).fetchall()
        finally:
            connection.close()
        samples = [row[0] for row in rows if row[0] is not None and math.isfinite(row[0])]
        if not samples:
            return EvaluationCostPreview(case_count, request_count, None, 0)
        average = sum(samples) / len(samples)
        return EvaluationCostPreview(case_count, request_count, average * request_count, len(samples))
