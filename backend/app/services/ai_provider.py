"""Explicit provider selection; no automatic fallback or cross-provider history."""
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.services.ai_observability import call_budget
from time import monotonic
import asyncio


async def complete(messages):
    if get_settings().ai_provider.strip().lower() != 'openai':
        raise AppError(503, 'AI_PROVIDER_CONFIGURATION', 'AI Chat yêu cầu OpenAI; không có provider dự phòng.')
    from app.services.openai_provider import complete as generate
    budget=call_budget.get()
    if budget is None:
        return await generate(messages)
    remaining=budget.deadline-monotonic()
    if budget.calls>=budget.max_calls or remaining<=0:
        raise AppError(422,'AI_TURN_BUDGET','Yêu cầu vượt phạm vi có thể kiểm chứng trong một lượt. Hãy chia thành nhóm tài liệu hoặc nội dung nhỏ hơn.')
    budget.calls+=1
    try:
        async with asyncio.timeout(remaining):
            return await generate(messages)
    except TimeoutError:
        raise AppError(504,'AI_TURN_TIMEOUT','Yêu cầu quá thời gian xử lý. Bạn có thể thử lại với phạm vi nhỏ hơn.') from None
