"""Fault-injection contract tests only; never evidence of live OpenAI acceptance."""
import unittest
import json
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
import httpx2
import openai
from pydantic import SecretStr
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.services.openai_provider import complete, record_usage, close_clients


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_stream_without_completed_event_never_publishes_delta(self):
        real_sdk=openai.AsyncOpenAI
        def handler(request):
            event={'type':'response.output_text.delta','delta':'Unverified partial tokens',
                'item_id':'msg_test','output_index':0,'content_index':0,'sequence_number':0}
            return httpx2.Response(200,headers={'content-type':'text/event-stream'},
                content='data: '+json.dumps(event)+'\n\n')
        settings=get_settings().model_copy(update={'openai_api_key':SecretStr('test-secret')})
        def sdk(**kwargs):
            return real_sdk(**kwargs,http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        with patch('app.services.openai_provider.get_settings',return_value=settings),patch(
                'app.services.openai_provider.AsyncOpenAI',side_effect=sdk):
            with self.assertRaises(AppError) as interrupted:
                await complete([{'role':'user','content':'Test incomplete stream'}])
        self.assertEqual(interrupted.exception.code,'AI_PROVIDER_INCOMPLETE')

    async def asyncTearDown(self):
        await close_clients()

    async def test_configuration_check_and_probe_gate_never_create_client(self):
        from scripts.verify_openai_connection import main
        settings = get_settings().model_copy(update={
            'ai_provider': 'openai', 'openai_model': 'gpt-6-luna',
            'openai_api_key': SecretStr('test-secret'), 'openai_timeout_seconds': 60})
        with patch('scripts.verify_openai_connection.get_settings', return_value=settings), patch(
                'scripts.verify_openai_connection.AsyncOpenAI') as sdk, patch('builtins.print'):
            self.assertEqual(await main([]), 0)
            self.assertEqual(await main(['--probe']), 1)
            self.assertEqual(await main(['--billing-confirmed']), 0)
            sdk.assert_not_called()

    async def test_real_sdk_serialization_and_output_text_without_network(self):
        payloads = []
        def handler(request):
            payloads.append(json.loads(request.content))
            response = {
                'id': 'resp_test', 'object': 'response', 'created_at': 0,
                'model': 'gpt-6-luna', 'status': 'completed',
                'output': [{'type': 'message', 'id': 'msg_test', 'status': 'completed',
                            'role': 'assistant', 'content': [{'type': 'output_text',
                            'text': '{"verdict":"accepted"}', 'annotations': []}]}],
                'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120,
                          'input_tokens_details': {'cached_tokens': 40, 'cache_write_tokens': 10},
                          'output_tokens_details': {'reasoning_tokens': 5}},
            }
            events = [{'type': 'response.output_text.delta', 'delta': '{"verdict":"accepted"}',
                       'item_id': 'msg_test', 'output_index': 0, 'content_index': 0, 'sequence_number': 0},
                      {'type': 'response.completed', 'response': response, 'sequence_number': 1}]
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'},
                content=''.join('data: '+json.dumps(event)+'\n\n' for event in events))
        settings = get_settings().model_copy(update={
            'openai_api_key': SecretStr('test-secret'), 'openai_model': 'gpt-6-luna'})
        def sdk(**kwargs):
            self.assertEqual(kwargs['max_retries'], 0)
            self.assertEqual(kwargs['timeout'], 60)
            return openai.AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(handler)))
        with patch('app.services.openai_provider.get_settings', return_value=settings), patch(
                'app.services.openai_provider.AsyncOpenAI', side_effect=sdk) as factory, self.assertLogs(
                'uvicorn.error.openai_usage', level='INFO') as logs:
            self.assertEqual(await complete([{'role': 'user', 'content': 'private JSON'}]),
                             {'verdict': 'accepted'})
            self.assertEqual(await complete([{'role': 'user', 'content': 'private JSON'}]),
                             {'verdict': 'accepted'})
            factory.assert_called_once()
        self.assertEqual(len(payloads), 2)
        body = payloads[0]
        self.assertEqual(body['reasoning'], {'effort': 'none'})
        self.assertTrue(body['stream'])
        self.assertEqual(body['max_output_tokens'], 2048)
        self.assertFalse(body['store'])
        for forbidden in ('temperature', 'max_tokens', 'reasoning_effort', 'tools', 'previous_response_id'):
            self.assertNotIn(forbidden, body)
        entry = json.loads(logs.output[0].split('openai_usage ', 1)[1])
        self.assertEqual((entry['input_tokens'], entry['output_tokens'], entry['cached_tokens']), (100, 20, 40))
        self.assertAlmostEqual(entry['estimated_cost_usd'], 0.00001665)
        self.assertNotIn('test-secret', str(logs.output))
        self.assertNotIn('private JSON', str(logs.output))

    def test_usage_missing_unknown_model_and_incomplete(self):
        with patch('app.services.openai_provider.logger.info') as log:
            record_usage(SimpleNamespace(usage=None), 'gpt-6-luna')
            log.assert_not_called()
            response = SimpleNamespace(status='incomplete', usage=SimpleNamespace(
                input_tokens=100, output_tokens=20, input_tokens_details=None))
            record_usage(response, 'other-model')
            self.assertIsNone(json.loads(log.call_args.args[1])['estimated_cost_usd'])
            record_usage(response, 'gpt-6-luna')
            self.assertAlmostEqual(json.loads(log.call_args.args[1])['estimated_cost_usd'], 0.00002)

    def test_usage_long_context_premium_is_included_in_telemetry(self):
        response=SimpleNamespace(usage=SimpleNamespace(input_tokens=280_000,output_tokens=20,input_tokens_details=None))
        with patch('app.services.openai_provider.logger.info') as log:
            record_usage(response,'gpt-6-luna')
        self.assertAlmostEqual(json.loads(log.call_args.args[1])['estimated_cost_usd'],0.056015)

    async def test_missing_configuration_without_network(self):
        for key, model, code in [('', '', 'AI_API_KEY_MISSING'), ('test-secret', '', 'AI_MODEL_MISSING')]:
            settings = get_settings().model_copy(update={'openai_api_key': SecretStr(key), 'openai_model': model})
            with patch('app.services.openai_provider.get_settings', return_value=settings):
                with self.assertRaises(AppError) as result:
                    await complete([])
            self.assertEqual(result.exception.code, code)

    async def test_sdk_error_classification_and_no_secret_disclosure(self):
        request = httpx2.Request('POST', 'https://api.openai.com/v1/responses')
        def status_error(kind, status, code=None, error_type=None):
            return kind('test-secret must never appear', response=httpx2.Response(status, request=request), body={'code': code, 'type': error_type})
        cases = [
            (openai.APITimeoutError(request=request), 'AI_PROVIDER_TIMEOUT'),
            (openai.APIConnectionError(request=request), 'AI_PROVIDER_NETWORK'),
            (status_error(openai.AuthenticationError, 401), 'AI_API_KEY_INVALID'),
            (status_error(openai.RateLimitError, 429, 'insufficient_quota'), 'AI_QUOTA_EXCEEDED'),
            (status_error(openai.RateLimitError, 429, 'credit_balance_exhausted', 'insufficient_quota'), 'AI_QUOTA_EXCEEDED'),
            (status_error(openai.RateLimitError, 429, None, 'insufficient_quota'), 'AI_QUOTA_EXCEEDED'),
            (status_error(openai.RateLimitError, 429, 'rate_limit_exceeded'), 'AI_RATE_LIMITED'),
            (status_error(openai.NotFoundError, 404), 'AI_MODEL_UNAVAILABLE'),
            (status_error(openai.InternalServerError, 500), 'AI_PROVIDER_ERROR'),
        ]
        settings = get_settings().model_copy(update={'openai_api_key': SecretStr('test-secret'), 'openai_model': 'test-model'})
        for fault, code in cases:
            client = AsyncMock()
            client.__aenter__.return_value = client
            client.responses.create.side_effect = fault
            with self.subTest(code=code), patch('app.services.openai_provider.get_settings', return_value=settings), patch('app.services.openai_provider.AsyncOpenAI', return_value=client):
                with self.assertRaises(AppError) as result:
                    await complete([])
                self.assertEqual(result.exception.code, code)
                self.assertNotIn('test-secret', str(result.exception))
                client.responses.create.assert_awaited_once()

    async def test_incomplete_and_invalid_json_fail_closed(self):
        settings = get_settings().model_copy(update={'openai_api_key': SecretStr('test-secret'), 'openai_model': 'test-model'})
        for status, output, code in [('incomplete', '{}', 'AI_PROVIDER_INCOMPLETE'), ('completed', 'not JSON', 'AI_PROVIDER_INVALID_RESPONSE')]:
            client = AsyncMock(); client.__aenter__.return_value = client
            class FakeStream:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
                def __aiter__(self): return self.events()
                async def events(self):
                    yield SimpleNamespace(type='response.completed' if status == 'completed' else 'response.incomplete',
                                          response=SimpleNamespace(status=status, output_text=output))
            client.responses.create.return_value = FakeStream()
            with patch('app.services.openai_provider.get_settings', return_value=settings), patch('app.services.openai_provider.AsyncOpenAI', return_value=client):
                with self.assertRaises(AppError) as result:
                    await complete([])
                self.assertEqual(result.exception.code, code)
                self.assertFalse(client.responses.create.call_args.kwargs['store'])
