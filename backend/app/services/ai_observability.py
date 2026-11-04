"""Bounded, process-local progress and numeric timings; no prompts or secrets."""
from contextvars import ContextVar
from time import perf_counter, monotonic
from dataclasses import dataclass

trace: ContextVar[dict | None] = ContextVar('ai_trace', default=None)
reviewing: ContextVar[bool] = ContextVar('ai_reviewing', default=False)
output_format: ContextVar[dict | None] = ContextVar('ai_output_format', default=None)
output_token_limit: ContextVar[int] = ContextVar('ai_output_token_limit', default=2048)
generation_effort: ContextVar[str] = ContextVar('ai_generation_effort', default='none')
v2_usage_sink: ContextVar[list | None] = ContextVar('v2_usage_sink', default=None)
v2_natural_output: ContextVar[bool] = ContextVar('v2_natural_output', default=False)
v2_tool_schemas: ContextVar[list | None] = ContextVar('v2_tool_schemas', default=None)
v2_provider_request: ContextVar[bool] = ContextVar('v2_provider_request', default=False)

@dataclass
class TurnBudget:
    max_calls:int
    deadline:float
    calls:int=0

call_budget:ContextVar[TurnBudget|None]=ContextVar('ai_call_budget',default=None)
progress = {}


def stage(key, name):
    now = monotonic()
    for stale in [k for k, v in progress.items() if now-v[1] > 600]:
        progress.pop(stale, None)
    if len(progress) >= 1024:
        progress.pop(next(iter(progress)))
    progress[key] = (name, now)


def mark(name, started):
    current = trace.get()
    if current is not None:
        current[name] = round((perf_counter()-started)*1000, 2)
