"""Local acceptance server against dedicated kltn_test, never user DB."""
import os
from pathlib import Path
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

root=Path(__file__).resolve().parents[1]
url=dotenv_values(root/'.env').get('TEST_DATABASE_URL','')
if make_url(url).database!='kltn_test':
    raise RuntimeError('Requires dedicated kltn_test')
os.environ['DATABASE_URL']=url
os.environ['DOCUMENT_VISION_LEDGER']=str(root/'.runtime'/'multimodal-acceptance.sqlite3')
os.environ['DOCUMENT_VISION_DAILY_CALL_CAP']='40'
from app.core.config import get_settings
get_settings.cache_clear()

if __name__=='__main__':
    import uvicorn
    uvicorn.run('app.main:app',host='127.0.0.1',port=8015,log_level='warning',access_log=False)
