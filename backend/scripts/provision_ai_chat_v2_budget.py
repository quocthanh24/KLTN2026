"""Operator provisioning only. Preview by default; does NOT call any model API.

Run --provision only AFTER separate explicit owner approval of model/scope/cap.
No old approval is copied. Input/output rates must be verified upper bounds.
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.config import get_settings
from app.services.ai_chat_v2_budget import BudgetLedger

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--approval-id',required=True)
parser.add_argument('--channel',type=UUID,required=True)
parser.add_argument('--model',required=True)
parser.add_argument('--cap-microusd',type=int,required=True)
parser.add_argument('--job-cap-microusd',type=int,required=True)
parser.add_argument('--input-rate-microusd-per-million',type=int,required=True)
parser.add_argument('--output-rate-microusd-per-million',type=int,required=True)
parser.add_argument('--expires-at',required=True,help='ISO timestamp with explicit timezone')
parser.add_argument('--concurrency',type=int,default=1)
parser.add_argument('--provision',action='store_true')
args=parser.parse_args()
settings=get_settings()
if args.model!=settings.openai_model:parser.error('Model must exactly match existing configured model; this script never changes it')
expires=datetime.fromisoformat(args.expires_at)
if expires.tzinfo is None:parser.error('Expiry timezone is required')
if not args.provision:
    print(json.dumps({'action':'PREVIEW ONLY - no approval inserted','model':args.model,'channel':str(args.channel),
        'cap_microusd':args.cap_microusd,'job_cap_microusd':args.job_cap_microusd,
        'concurrency':args.concurrency,'expires_at':expires.isoformat()}))
else:
    BudgetLedger(settings.ai_chat_v2_budget_ledger).approve(args.approval_id,args.channel,args.model,
        args.cap_microusd,args.job_cap_microusd,args.concurrency,expires.timestamp(),
        args.input_rate_microusd_per_million,args.output_rate_microusd_per_million)
    print('Scoped approval provisioned. Feature flags remain unchanged. No model API was called.')
