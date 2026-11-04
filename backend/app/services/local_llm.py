"""Cached local E5 embedding shared by ingestion and V2 navigation."""
import hashlib
from pathlib import Path
import threading
from functools import lru_cache
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.services.document_pipeline import ARTIFACT_HASHES, MODEL_FILE, e5_vector

_embedding_lock = threading.Lock()


def warm_encoder():
    with _embedding_lock:
        encoder()


@lru_cache(maxsize=1)
def encoder():
    import onnxruntime as ort
    from tokenizers import Tokenizer
    root=Path(get_settings().rag_model_dir)
    for name, expected in ARTIFACT_HASHES.items():
        with (root/name).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=expected:
                raise ValueError('artifact mismatch')
    options=ort.SessionOptions(); options.intra_op_num_threads=2; options.inter_op_num_threads=1
    return Tokenizer.from_file(str(root/'tokenizer.json')),ort.InferenceSession(str(root/MODEL_FILE),sess_options=options,providers=['CPUExecutionProvider'])


def query_vector(question):
    with _embedding_lock:
        tokenizer,session=encoder()
        tokens=tokenizer.encode('query: '+question)
        if len(tokens.ids)>512: raise AppError(422,'AI_QUESTION_TOO_LONG','Câu hỏi vượt giới hạn token. Vui lòng rút gọn.')
        return e5_vector(session,tokens)
