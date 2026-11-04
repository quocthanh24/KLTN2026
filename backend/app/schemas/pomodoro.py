from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

from app.core.pomodoro import DEFAULT_FOCUS_SECONDS, DEFAULT_BREAK_SECONDS, MIN_DURATION_SECONDS, MAX_DURATION_SECONDS
Duration = Annotated[int, Field(strict=True, ge=MIN_DURATION_SECONDS, le=MAX_DURATION_SECONDS)]


class PomodoroMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "pause", "resume", "end"]
    revision: Annotated[int, Field(strict=True, ge=0)]
    focus_seconds: Duration = DEFAULT_FOCUS_SECONDS
    break_seconds: Duration = DEFAULT_BREAK_SECONDS
