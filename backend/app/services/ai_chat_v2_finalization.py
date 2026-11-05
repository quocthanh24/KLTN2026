"""Technical source checks for natural answers, not a teaching/style editor.

General model knowledge requires no document quote or exhaustive claim schema.
Only source annotations/citations and explicitly reviewed document assertions
are constrained by evidence. Semantic source attribution still needs model and
human evaluation; this module does not pretend to infer intent from regexes.
"""
import json
import re
from app.core.exceptions import AppError

SOURCE_LINK=re.compile(r'\[([^\]]*)\]\(#source-(\d+)\)')
FOOTER=re.compile(r'\s*<source_refs>\s*(.*?)\s*</source_refs>\s*$',re.S)
ACTION=re.compile(r'\s*<tool_call>\s*(.*?)\s*</tool_call>\s*',re.S)
CODE=re.compile(r'(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*(?:\n|$)|`+[^`\n]*`+')


def source_links(text):
    ranges=[(m.start(),m.end()) for m in CODE.finditer(text)]
    return [m for m in SOURCE_LINK.finditer(text) if not any(a<=m.start()<b for a,b in ranges)]


def transform_links(text,replace):
    allowed={m.start() for m in source_links(text)}
    return SOURCE_LINK.sub(lambda match:replace(match) if match.start() in allowed else match.group(0),text)


def normalize_reply(value):
    """Accept free Markdown and older answer envelopes; claims are OPTIONAL."""
    issues=[]
    if isinstance(value,str):
        action=ACTION.fullmatch(value)
        if action:
            try:
                parsed=json.loads(action.group(1))
                if isinstance(parsed,dict) and isinstance(parsed.get('tool'),str) and isinstance(parsed.get('arguments'),dict):
                    # Internal native transport fields come only from the SDK
                    # adapter, never from model-written compatibility JSON.
                    return {'tool':parsed['tool'],'arguments':parsed['arguments']}
            except (ValueError,TypeError):pass
            return {'protocol_error':'TOOL_ERROR'}
        # Ordinary text (including JSON requested by the user) is presentation,
        # not an implicit tool command or a mandatory answer envelope.
    if isinstance(value,dict) and 'tool' in value:
        return value
    if isinstance(value,dict):
        body=value.get('answer',value.get('text'))
        if not isinstance(body,str):body=json.dumps(value,ensure_ascii=False)
        annotation=dict(value.get('source_refs',{})) if isinstance(value.get('source_refs'),dict) else {}
        for key in ('task','source_only','partial','missing_source','claimed_document_facts'):
            if key in value:annotation[key]=value[key]
        citations=value.get('citations',annotation.get('citations',[]))
        old_claims=value.get('claims',[]) if isinstance(value.get('claims',[]),list) else []
    elif isinstance(value,str):
        body=value;annotation={};citations=[];old_claims=[]
    else:
        raise AppError(503,'AI_V2_MODEL_EMPTY','Model did not return a readable answer.')
    footer=FOOTER.search(body)
    if footer:
        body=body[:footer.start()]
        try:
            decoded=json.loads(footer.group(1))
            if not isinstance(decoded,dict):raise ValueError()
            annotation.update(decoded);citations=decoded.get('citations',citations)
        except (ValueError,TypeError):pass # Optional formatting metadata is not a factual failure.
    if not body.strip():
        raise AppError(503,'AI_V2_MODEL_EMPTY','Model returned no answer text.')
    if len(body)>60000:
        raise AppError(422,'AI_V2_ANSWER_LIMIT','Answer exceeds the bounded response window.')
    if not isinstance(citations,list) or len(citations)>80:
        citations=[];issues.append('CITATION_INVALID')
    if not isinstance(annotation.get('task',''),str):annotation.pop('task',None)
    for field in ('partial','source_only','missing_source','claimed_document_facts'):
        if field in annotation and not isinstance(annotation[field],bool):
            if isinstance(annotation[field],str) and annotation[field].lower() in {'true','false'}:
                annotation[field]=annotation[field].lower()=='true'
            else:annotation.pop(field)
    return {'answer':body,'citations':citations,'annotations':annotation,'legacy_claims':old_claims,'issues':issues}


def technical_citations(reply,context):
    valid={};invalid=set();issues=list(reply['issues'])
    for index,citation in enumerate(reply['citations']):
        if not isinstance(citation,dict) or not isinstance(citation.get('source_id'),str):
            invalid.add(index);continue
        source=context.get(citation['source_id']);quote=citation.get('quote')
        if (source is None or not isinstance(quote,str) or not 1<=len(quote)<=800 or source.get('authority')=='derived_navigation'
                or (source.get('representation',{}).get('kind')!='image' and quote not in source['text'])
                or ('revision' in citation and citation['revision']!=source.get('revision'))
                or ('location' in citation and citation['location']!=source.get('location'))):
            invalid.add(index);continue
        valid[index]={'source_id':citation['source_id'],'quote':quote}
    linked={int(m.group(2)) for m in source_links(reply['answer'])}
    invalid.update(linked-set(valid))
    if invalid:issues.append('CITATION_INVALID')
    return valid,invalid,list(dict.fromkeys(issues))


def source_review_needed(reply,tools):
    annotation=reply['annotations']
    return bool(getattr(tools,'source_selection',None) or getattr(tools,'original_requested',False) or tools.context or getattr(tools,'images',[]) or reply['citations'] or reply['issues']
        or source_links(reply['answer']) or getattr(getattr(tools,'evidence',None),'whole',False)
        or annotation.get('task') in {'grounded','whole'} or annotation.get('source_only') is True
        or annotation.get('claimed_document_facts') is True
        or any(isinstance(c,dict) and isinstance(c.get('kind'),str) and c['kind'] in {'source','mixed'} for c in reply['legacy_claims']))


def answer_span(text,answer):
    """Map formatting-only differences to one exact raw span; never paraphrase."""
    if text in answer:return text
    def visible(raw):
        skipped=set()
        for match in re.finditer(r'(\*\*|__|`)([^\n]+?)\1',raw):
            width=len(match.group(1))
            skipped.update(range(match.start(),match.start()+width))
            skipped.update(range(match.end()-width,match.end()))
        positions=[i for i in range(len(raw)) if i not in skipped]
        return ''.join(raw[i] for i in positions),positions,skipped
    plain,positions,skipped=visible(answer);needle,_,_=visible(text)
    if not needle or plain.count(needle)!=1:return None
    offset=plain.index(needle);start=positions[offset];end=positions[offset+len(needle)-1]+1
    while start and start-1 in skipped:start-=1
    while end<len(answer) and end in skipped:end+=1
    return answer[start:end]


def answer_blocks(answer):
    lines=answer.splitlines();blocks=[];index=0
    while index<len(lines):
        start=index;line=lines[index]
        fence=re.match(r'^\s*(`{3,}|~{3,})',line)
        if fence:
            index+=1
            while index<len(lines):
                closing=lines[index].lstrip().startswith(fence.group(1));index+=1
                if closing:break
        elif '|' in line and index+1<len(lines) and re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*',lines[index+1]):
            index+=2
            while index<len(lines) and lines[index].strip() and '|' in lines[index]:index+=1
        else:index+=1
        if line.strip():blocks.append({'id':f'b{start}','text':'\n'.join(lines[start:index])})
    return blocks


def review_format(block_ids=None):
    def obj(properties):return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}
    string={'type':'string'};boolean={'type':'boolean'}
    block={'type':'string','enum':list(block_ids)} if block_ids else string
    claim=obj({'block_id':block,'kind':{'type':'string','enum':['document_fact','general_knowledge','reasoning','example']},
        'verdict':{'type':'string','enum':['supported','unsupported','uncertain']},
        'citation_indices':{'type':'array','items':{'type':'integer'}},'official':boolean})
    schema=obj({'source_claims':{'type':'array','items':claim},'all_document_claims_reviewed':boolean,
        'citation_errors':{'type':'array','items':{'type':'integer'}},
        'constraint_violations':{'type':'array','items':obj({'block_id':block,'reason':string})},
        'required_scope':{'type':'string','enum':['whole','focused','general','conversation']},'scope_resolved':boolean,
        'required_document_ids':{'type':'array','items':string},'source_task_complete':boolean,
        'children':{'type':'array','items':obj({'id':string,'covered':boolean,'taught_in_answer':string})},'security_violation':boolean})
    return {'type':'json_schema','name':'source_review','strict':True,'schema':schema}


def source_review(value,reply):
    """Validate only document-claim review. General prose needs no claim inventory.

    Older in-flight checkpoint/test adapters can provide their old verdicts;
    those are translated for source assertions, never used to quote-check
    supplemental reasoning or to reconstruct the entire answer.
    """
    if not isinstance(value,dict):return None
    if 'source_claims' not in value and isinstance(value.get('claims'),list) and reply['legacy_claims']:
        verdicts={}
        for item in value['claims']:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in verdicts:return None
            verdicts[item['id']]=item.get('verdict')
        translated=[]
        for claim in reply['legacy_claims']:
            if not isinstance(claim,dict) or not isinstance(claim.get('kind'),str) or claim['kind'] not in {'source','mixed'}:continue
            translated.append({'text':claim.get('text'),'verdict':verdicts.get(claim.get('id'),'uncertain'),
                'citation_indices':[i for i,c in enumerate(reply['citations']) if isinstance(c,dict) and c.get('claim_id')==claim.get('id')]})
        value={**value,'source_claims':translated,'all_document_claims_reviewed':True,
            'required_scope':value.get('required_scope','focused'),'scope_resolved':value.get('scope_resolved',True)}
    claims=value.get('source_claims')
    if not isinstance(claims,list) or len(claims)>240 or value.get('all_document_claims_reviewed') is not True:
        return None
    blocks={b['id']:b['text'] for b in answer_blocks(reply['answer'])}
    claims=[{**claim,'text':blocks.get(claim['block_id'])}
        if isinstance(claim,dict) and isinstance(claim.get('block_id'),str) else claim for claim in claims]
    claims=[{**claim,'text':answer_span(claim['text'],reply['answer'])}
        if isinstance(claim,dict) and isinstance(claim.get('text'),str) else claim for claim in claims]
    value={**value,'source_claims':claims}
    for claim in claims:
        if (not isinstance(claim,dict) or not isinstance(claim.get('text'),str) or not claim['text'].strip()
                or claim['text'] not in reply['answer'] or claim.get('verdict') not in {'supported','unsupported','uncertain'}
                or not isinstance(claim.get('citation_indices',[]),list)
                or any(type(i) is not int or i<0 for i in claim.get('citation_indices',[]))):return None
    violations=value.get('constraint_violations',[])
    if isinstance(violations,list):
        violations=[{**item,'text':blocks.get(item['block_id'])}
            if isinstance(item,dict) and isinstance(item.get('block_id'),str) else item for item in violations]
        value={**value,'constraint_violations':violations}
    if not isinstance(violations,list) or any(not isinstance(v,dict) or not isinstance(v.get('text'),str)
        or not v['text'].strip() or v['text'] not in reply['answer'] for v in violations):return None
    for field in ('children','citation_errors'):
        if not isinstance(value.get(field,[]),list):value={**value,field:[]}
    if not isinstance(value.get('required_scope','general'),str):value={**value,'required_scope':'general'}
    return value


def finish_sources(reply,review,valid,invalid):
    """Remove only identified unsupported assertions/links; preserve other prose."""
    body=reply['answer'];removed=[];supported=[]
    bad_citations=set(invalid)
    for index in review.get('citation_errors',[]):
        if type(index) is int:bad_citations.add(index)
    for claim in review['source_claims']:
        # This classification is supplied by the independent source review,
        # never by writer annotations. General teaching requires no file quote.
        if claim.get('kind') in {'general_knowledge','reasoning','example'} and claim.get('official') is not True:
            continue
        refs=claim.get('citation_indices',[])
        if claim['verdict']!='supported' or not refs or any(i not in valid or i in bad_citations for i in refs):
            removed.append(claim['text'])
        else:supported.append(claim)
    removed.extend(v['text'] for v in review.get('constraint_violations',[]))
    for text in sorted(set(removed),key=len,reverse=True):body=body.replace(text,'')
    # Attach a missing verified link without rewriting a heading, example or explanation.
    used=set()
    for claim in supported:
        if claim['text'] not in body:continue
        used.update(claim.get('citation_indices',[]))
        for index in claim.get('citation_indices',[]):
            if not any(int(m.group(2))==index for m in source_links(body)):
                body=body.replace(claim['text'],claim['text']+f' [{index+1}](#source-{index})',1)
    kept={i:c for i,c in valid.items() if i not in bad_citations and i in used}
    mapping={old:new for new,old in enumerate(sorted(kept))}
    def link(match):
        old=int(match.group(2))
        if old in mapping:return f'[{match.group(1)}](#source-{mapping[old]})'
        return '' if match.group(1).strip().isdigit() else match.group(1)
    body=transform_links(body,link)
    return body,[kept[i] for i in sorted(kept)],bool(removed or bad_citations),supported
