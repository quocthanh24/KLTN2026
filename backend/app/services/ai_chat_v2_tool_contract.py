"""Responses function declarations for existing authorized V2 tools, no hosted tools."""
import base64
import hashlib
import json
from app.core.exceptions import AppError

PROPERTIES={
    'document_id':{'type':'string','description':'Copy the exact document_id UUID from the CURRENT authorized catalog, never a filename or historical ID.'},'document_ids':{'type':'array','items':{'type':'string'},'maxItems':20},
    'revision':{'type':'string','description':'Copy the FULL exact 64-character revision from the SAME catalog document. Never abbreviate.'},'query':{'type':'string'},'offset':{'type':'integer','minimum':0,'maximum':100000},
    'units':{'type':'array','items':{'type':'integer','minimum':1,'maximum':10000},'minItems':1,'maxItems':2},
    'pages':{'type':'array','items':{'type':'integer','minimum':1}},'page':{'type':'integer','minimum':1},
    'chunk_ids':{'type':'array','items':{'type':'string'}},'region_id':{'type':'string'},'original':{'type':'boolean'},
    'children':{'type':'array','items':{'type':'object'}}}
CONTRACTS={
    'list_available_documents':('List current authorized Channel documents. Index failure does not imply original-reader failure.',['offset'],[]),
    'source_inventory':('Inspect original source unit/page inventory, even with zero chunks or failed/partial indexing. Inventory is not content read.',['document_id','revision','offset'],['document_id','revision']),
    'read_original_units':('Read specific original units from authorized storage, independent of index status. PDF physical pages are delivered as actual images; at most two. Use for exact exercises, pictures, tables and layout.',['document_id','revision','units'],['document_id','revision','units']),
    'get_document_outline':('Navigation outline, not proof of original content.',['document_id','offset'],['document_id']),
    'search_documents':('Search derived navigation aids. No hits or zero chunks do not rule out original reading.',['query','document_id'],['query']),
    'read_document_pages':('Read indexed page navigation, not original-image authority.',['document_id','pages','offset'],['document_id','pages']),
    'read_document_segments':('Read indexed segments as navigation.',['chunk_ids'],['chunk_ids']),
    'read_document_batch':('Read derived navigation across explicit documents.',['document_ids','offset'],['document_ids']),
    'begin_whole_document':('Begin bounded whole-source inventory only when user requests whole scope.',['document_ids'],['document_ids']),
    'register_child_obligations':('Register requested source task obligations.',['children'],['children']),
    'inspect_page_image':('Inspect authorized original page or image with revision checks.',['document_id','revision','page','region_id','original'],['document_id','revision']),
    'direct_pdf_capability':('Check direct-file support; never assume supported. ',[],[])}

def tool_schemas(catalog=None):
    import copy
    properties=copy.deepcopy(PROPERTIES)
    from uuid import UUID
    documents=[]
    for doc in (catalog or {}).get('documents',[]):
        try:
            UUID(doc['document_id'])
            if not isinstance(doc.get('revision'),str) or len(doc['revision'])!=64:continue
            documents.append(doc)
        except (ValueError,TypeError,KeyError):continue
    if documents:
        properties['document_id']['enum']=[d['document_id'] for d in documents]
        properties['revision']['enum']=list(dict.fromkeys(d['revision'] for d in documents))
    return [{'type':'function','name':name,'description':description,'strict':False,
        'parameters':{'type':'object','properties':{k:properties[k] for k in fields},
                      'required':required,'additionalProperties':False}}
        for name,(description,fields,required) in CONTRACTS.items()]

def require_original_images(messages,expected):
    """Validate again immediately before SDK serialization, never log raw input."""
    actual=[]
    for message in messages:
        for part in message.get('content',[]) if isinstance(message.get('content'),list) else []:
            if part.get('type')=='input_image':
                try:
                    prefix,encoded=part['image_url'].split(',',1)
                    if prefix!='data:image/jpeg;base64':raise ValueError()
                    raw=base64.b64decode(encoded,validate=True)
                    actual.append(hashlib.sha256(raw).hexdigest())
                except (KeyError,ValueError,TypeError):
                    raise AppError(422,'AI_V2_ORIGINAL_IMAGE_INVALID','Invalid original image input.') from None
    from app.core.config import get_settings
    if len(actual)>get_settings().ai_chat_v2_max_images:
        raise AppError(422,'AI_V2_IMAGE_LIMIT','Original image request limit reached.')
    if any(digest not in actual for digest in expected):
        raise AppError(422,'AI_V2_ORIGINAL_IMAGE_MISSING','Original image evidence did not reach model input; cannot verify source facts.')

def expected_images(messages):
    """Provenance requirements embedded separately from image bytes; survives dropped parts."""
    expected=[]
    for message in messages:
        content=message.get('output') if message.get('type')=='function_call_output' else message.get('content')
        text=content if isinstance(content,str) else (content[0].get('text','') if isinstance(content,list) and content else '')
        try:payload=json.loads(text)
        except (ValueError,TypeError):continue
        if not isinstance(payload,dict):continue
        passages=payload.get('tool_result',{}).get('passages',[]) if isinstance(payload.get('tool_result'),dict) else payload.get('evidence',[])
        for p in passages:
            if isinstance(p,dict) and p.get('authority')=='original' and p.get('representation',{}).get('kind')=='image':
                digest=p.get('representation_sha256')
                if not isinstance(digest,str) or len(digest)!=64:
                    raise AppError(422,'AI_V2_ORIGINAL_IMAGE_INVALID','Missing image provenance hash.')
                expected.append(digest)
    return expected

def payload_metadata(messages,schemas=None):
    """Redacted pre-dispatch receipt; the SDK transport test checks serialized bytes too."""
    images=[];types=[]
    for message in messages:
        content=message.get('content')
        if isinstance(content,str):types.append('text');continue
        for part in content or []:
            types.append(part.get('type'))
            if part.get('type')=='input_image':
                prefix,encoded=part['image_url'].split(',',1)
                raw=base64.b64decode(encoded,validate=True)
                images.append({'mime_type':prefix.removeprefix('data:').split(';')[0],
                    'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'detail':part.get('detail')})
    return {'content_types':types,'images':images,'tool_schema_names':[s['name'] for s in schemas or []],
        'tool_schema_sha256':hashlib.sha256(json.dumps(schemas or [],sort_keys=True).encode()).hexdigest(),
        'system_prompt_sha256':hashlib.sha256(str(messages[0].get('content','') if messages else '').encode()).hexdigest()}
