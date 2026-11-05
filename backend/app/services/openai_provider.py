"""OpenAI generation only; retrieval, authorization and evidence remain local."""
import asyncio
import json
import logging
from uuid import uuid4
from weakref import WeakKeyDictionary
from time import perf_counter

from openai import AsyncOpenAI, APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.services.ai_observability import trace, reviewing, output_format, output_token_limit, generation_effort, v2_natural_output


logger = logging.getLogger('uvicorn.error.openai_usage')
_clients = WeakKeyDictionary()


def get_client(settings, key):
    # A pool belongs to one event loop and credential/factory. Never reuse a
    # connection from a closed loop or keep reading files on every request.
    loop = asyncio.get_running_loop()
    clients = _clients.setdefault(loop, {})
    identity = (key, AsyncOpenAI)
    if identity not in clients:
        clients[identity] = AsyncOpenAI(api_key=key, base_url='https://api.openai.com/v1',
                                       timeout=settings.openai_timeout_seconds,max_retries=0)
    return clients[identity]


async def close_clients():
    clients=_clients.pop(asyncio.get_running_loop(),{})
    for client in clients.values():
        await client.close()


def record_usage(response, model):
    """Only numeric usage and a local correlation ID; never log SDK objects/payloads.

    Standard USD rates verified 2026-11-02. Estimates exclude taxes; unknown
    models/tiers have no estimate rather than inheriting Luna's pricing.
    """
    usage = getattr(response, 'usage', None)
    from app.services.ai_observability import v2_usage_sink
    sink = v2_usage_sink.get()
    if sink is not None and usage is not None:
        sink.append({'model': getattr(response, 'model', None),
            'request_id': getattr(response, '_request_id', None),
            'response_id': getattr(response, 'id', None),
            'input_tokens': usage.input_tokens, 'output_tokens': usage.output_tokens})
    if usage is None:
        return
    details = getattr(usage, 'input_tokens_details', None)
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cached_tokens = getattr(details, 'cached_tokens', 0) or 0
    cache_write_tokens = getattr(details, 'cache_write_tokens', 0) or 0
    cost = None
    if model == 'gpt-6-luna' and getattr(response, 'service_tier', None) in {None, 'default'}:
        # The long-context premium applies to the entire request, including
        # cache rates, once input exceeds 272K. This is telemetry only; monetary
        # enforcement uses independently approved conservative ledger rates.
        input_multiplier = 2 if input_tokens > 272_000 else 1
        output_multiplier = 1.5 if input_tokens > 272_000 else 1
        cost = ((max(0, input_tokens - cached_tokens - cache_write_tokens) * 0.10
                + cached_tokens * 0.01 + cache_write_tokens * 0.125) * input_multiplier
                + output_tokens * 0.50 * output_multiplier) / 1_000_000
    metrics={
        'response_model': getattr(response,'model',None),
        'request_id': getattr(response,'_request_id',None), 'correlation_id': str(uuid4()), 'input_tokens': input_tokens,
        'output_tokens': output_tokens, 'cached_tokens': cached_tokens,
        'cache_write_tokens': cache_write_tokens, 'estimated_cost_usd': cost,
        'pricing_date': '2026-11-02',
    }
    current=trace.get()
    if current is not None:
        current.setdefault('usage',[]).append(metrics)
    logger.info('openai_usage %s', json.dumps(metrics))


async def complete(messages):
    settings = get_settings()
    key = settings.openai_api_key.get_secret_value().strip()
    if not key:
        raise AppError(503, 'AI_API_KEY_MISSING', 'Chưa cấu hình OpenAI API key. Quản trị viên cần nhập OPENAI_API_KEY trong backend/.env rồi khởi động lại backend.')
    if not settings.openai_model.strip():
        raise AppError(503, 'AI_MODEL_MISSING', 'Chưa chọn mô hình OpenAI. Hãy cấu hình OPENAI_MODEL trong backend/.env rồi khởi động lại backend.')
    try:
        # Fixed official endpoint; no environment-controlled proxy provider, tools or remote history.
        async with asyncio.timeout(settings.openai_timeout_seconds):
                client = get_client(settings,key)
                start=perf_counter()
                from app.services.ai_observability import v2_tool_schemas,v2_provider_request
                from app.services.ai_chat_v2_tool_contract import require_original_images,expected_images,payload_metadata
                schemas=v2_tool_schemas.get()
                if v2_provider_request.get():
                    require_original_images(messages,expected_images(messages))
                receipt=None
                if v2_provider_request.get() and trace.get() is not None:
                    receipt={'phase':'SDK_DISPATCH_PREPARED','model_requested':settings.openai_model.strip(),
                        **payload_metadata(messages,schemas)}
                    trace.get().setdefault('v2_provider_inputs',[]).append(receipt)
                stream = await client.responses.create(
                    model=settings.openai_model.strip(), input=messages, store=False,
                    reasoning={'effort': 'low' if reviewing.get() else generation_effort.get() if generation_effort.get() in {'low','medium'} else 'none'}, service_tier='default', stream=True,
                    max_output_tokens=min(4096,max(512,output_token_limit.get())),
                    text={'format': {'type':'text'} if v2_natural_output.get() else output_format.get() or {'type': 'json_object'}},
                    **({'tools':schemas,'tool_choice':'auto','parallel_tool_calls':False} if schemas else {}),
                )
                response=None
                first=None
                if receipt is not None:
                    http=getattr(stream,'response',None)
                    receipt['provider_request_id']=http.headers.get('x-request-id') if http is not None else None
                async with stream:
                    async for event in stream:
                        if event.type=='response.output_text.delta' and first is None:
                            first=round((perf_counter()-start)*1000,2)
                        if event.type in {'response.completed','response.incomplete'}:
                            response=event.response
                current=trace.get()
                if current is not None:
                    current.setdefault('openai_calls',[]).append({'first_token_ms':first,
                        'total_ms':round((perf_counter()-start)*1000,2)})
                if response is None:
                    raise AppError(503,'AI_PROVIDER_INCOMPLETE','OpenAI chưa trả về câu trả lời đầy đủ. Vui lòng thử lại.')
                http=getattr(stream,'response',None)
                if http is not None and not getattr(response,'_request_id',None):
                    response._request_id=http.headers.get('x-request-id')
                record_usage(response, settings.openai_model.strip())
                if receipt is not None:
                    receipt.update(actual_model=getattr(response,'model',None),response_id=getattr(response,'id',None),phase='PROVIDER_RESPONSE_RECEIVED')
                calls=[item for item in (getattr(response,'output',None) or []) if getattr(item,'type',None)=='function_call']
                if calls and schemas and response.status=='completed':
                    if len(calls)!=1 or calls[0].name not in {s['name'] for s in schemas}:
                        raise AppError(422,'AI_V2_TOOL_INVALID','Only one declared authorized tool per step.')
                    try:arguments=json.loads(calls[0].arguments)
                    except (ValueError,TypeError):raise AppError(422,'AI_V2_TOOL_INVALID','Invalid function arguments.') from None
                    if not isinstance(arguments,dict):raise AppError(422,'AI_V2_TOOL_INVALID','Tool arguments must be an object.')
                    # Replay the provider's output items, including reasoning, with
                    # the matching function_call_output in the next Responses input.
                    # Converting to a text tag loses call_id and the native protocol.
                    return {'tool':calls[0].name,'arguments':arguments,
                        '_provider_output':[item.model_dump(mode='json',exclude_none=True)
                            for item in response.output], '_call_id':calls[0].call_id}
                if response.status != 'completed' or not response.output_text:
                    raise AppError(503, 'AI_PROVIDER_INCOMPLETE', 'OpenAI chưa trả về câu trả lời đầy đủ. Vui lòng thử lại.')
                return response.output_text if v2_natural_output.get() else json.loads(response.output_text)
    except AppError:
        raise
    except (APITimeoutError, TimeoutError):
        raise AppError(504, 'AI_PROVIDER_TIMEOUT', 'OpenAI phản hồi quá lâu. Vui lòng thử lại sau.') from None
    except AuthenticationError:
        raise AppError(503, 'AI_API_KEY_INVALID', 'OpenAI API key không hợp lệ hoặc đã hết hiệu lực. Quản trị viên cần kiểm tra cấu hình backend.') from None
    except RateLimitError as error:
        quota = error.code in {'insufficient_quota', 'billing_hard_limit_reached', 'credit_balance_exhausted'} or error.type == 'insufficient_quota'
        raise AppError(503, 'AI_QUOTA_EXCEEDED' if quota else 'AI_RATE_LIMITED',
                       'Tài khoản OpenAI đã hết hạn mức. Quản trị viên cần kiểm tra thanh toán và quota.' if quota else
                       'OpenAI đang giới hạn số yêu cầu. Vui lòng đợi một lúc rồi thử lại.') from None
    except APIConnectionError:
        raise AppError(503, 'AI_PROVIDER_NETWORK', 'Backend không kết nối được OpenAI. Vui lòng kiểm tra mạng rồi thử lại.') from None
    except APIStatusError as error:
        if error.status_code in {403, 404}:
            raise AppError(503, 'AI_MODEL_UNAVAILABLE', 'Không thể truy cập mô hình OpenAI đã chọn. Quản trị viên cần kiểm tra OPENAI_MODEL và quyền API.') from None
        raise AppError(503, 'AI_PROVIDER_ERROR', 'Dịch vụ OpenAI chưa xử lý được yêu cầu. Vui lòng thử lại hoặc kiểm tra cấu hình mô hình.') from None
    except (ValueError, TypeError):
        raise AppError(503, 'AI_PROVIDER_INVALID_RESPONSE', 'Phản hồi OpenAI không đúng định dạng kiểm chứng. Chưa công bố câu trả lời; vui lòng thử lại.') from None
    except Exception:
        # Never expose SDK exceptions: they may contain request data or credentials.
        raise AppError(503, 'AI_PROVIDER_ERROR', 'Dịch vụ AI gặp lỗi xử lý. Vui lòng thử lại.') from None
