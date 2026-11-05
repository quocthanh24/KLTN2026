"""Shared exception identity across imported services and pipeline CLI."""


class PipelineFailure(Exception):
    page_number: int | None = None
    warning: str = ''
