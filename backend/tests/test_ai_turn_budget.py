"""Per-turn call/time bounds, including parallel synthesis. No network."""
import asyncio
import unittest
from time import monotonic
from unittest.mock import AsyncMock,patch
from app.services.ai_provider import complete
from app.services.ai_observability import call_budget,TurnBudget
from app.core.exceptions import AppError


class TurnBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_calls_share_limit_and_stop_before_upstream(self):
        upstream=AsyncMock(return_value={'answer':'ok'})
        budget=TurnBudget(3,monotonic()+20);token=call_budget.set(budget)
        try:
            with patch('app.services.openai_provider.complete',upstream):
                await asyncio.gather(*(complete([]) for _ in range(3)))
                with self.assertRaises(AppError) as error:await complete([])
                self.assertEqual(error.exception.code,'AI_TURN_BUDGET')
            self.assertEqual(upstream.await_count,3);self.assertEqual(budget.calls,3)
        finally:call_budget.reset(token)

    async def test_expired_deadline_does_not_make_a_provider_call(self):
        upstream=AsyncMock();token=call_budget.set(TurnBudget(3,monotonic()-1))
        try:
            with patch('app.services.openai_provider.complete',upstream),self.assertRaises(AppError):await complete([])
            upstream.assert_not_awaited()
        finally:call_budget.reset(token)
