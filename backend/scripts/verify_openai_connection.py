"""Mặc định chỉ kiểm tra cấu hình offline. Probe có phí cần xác nhận billing."""
import argparse
import asyncio
import json
import logging
from openai import AsyncOpenAI, APIStatusError
from app.core.config import get_settings
from app.services.openai_provider import record_usage


async def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe', action='store_true')
    parser.add_argument('--billing-confirmed', action='store_true')
    args = parser.parse_args(argv)
    s = get_settings()
    valid = (s.ai_provider == 'openai' and s.openai_model == 'gpt-6-luna'
             and s.openai_timeout_seconds == 60 and bool(s.openai_api_key))
    result = {'model': s.openai_model, 'provider': s.ai_provider,
              'timeout_seconds': s.openai_timeout_seconds, 'live': False,
              'status': 'READY_TO_TEST' if valid else 'CONFIGURATION_REQUIRED'}
    if not args.probe:
        print(json.dumps(result))
        return 0 if valid else 1
    if not args.billing_confirmed or not valid:
        result['status'] = 'BILLING_CONFIRMATION_REQUIRED' if valid else 'CONFIGURATION_REQUIRED'
        print(json.dumps(result))
        return 1
    # Exactly one synthetic request, no tools/retries; <=128 output tokens.
    logging.basicConfig(level=logging.WARNING)
    usage_logger = logging.getLogger('uvicorn.error.openai_usage')
    usage_logger.setLevel(logging.INFO)
    result['live'] = True
    try:
        async with AsyncOpenAI(api_key=s.openai_api_key.get_secret_value(),
                base_url='https://api.openai.com/v1', max_retries=0,
                timeout=s.openai_timeout_seconds) as client:
            response = await client.responses.create(model=s.openai_model,
                input='Return JSON: {"ok":true}', reasoning={'effort': 'none'},
                text={'format': {'type': 'json_object'}}, service_tier='default',
                max_output_tokens=128, store=False)
            record_usage(response, s.openai_model)
            result['status'] = ('completed' if response.status == 'completed'
                and json.loads(response.output_text) == {'ok': True} else 'blocked')
    except APIStatusError as error:
        # Do not print provider-controlled strings or raw exceptions.
        result.update(status='blocked', http_status=error.status_code,
                      quota_exceeded=error.code in {'insufficient_quota',
                      'billing_hard_limit_reached', 'credit_balance_exhausted'}
                      or error.type == 'insufficient_quota')
    except Exception:
        result['status'] = 'blocked'
    print(json.dumps(result))
    return 0 if result['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
