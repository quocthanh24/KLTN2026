"""Subject-independent provenance and semantic obligation state.

Inventory coverage is mechanical and reported separately from semantic review.
Mapping IDs alone never certifies a skill has been taught.
"""
import copy
import hashlib
from app.core.exceptions import AppError


def digest(text):
    return hashlib.sha256(text.encode('utf8')).hexdigest()


class EvidenceLedger:
    def __init__(self, value=None):
        value=copy.deepcopy(value or {})
        self.inventory=value.get('inventory',{})
        self.obligations=value.get('obligations',{})
        self.read=set(value.get('read',[]))
        self.whole=bool(value.get('whole',False))
        self.physical_inventory=value.get('physical_inventory',{})
        self.unsupported_scopes=value.get('unsupported_original_scopes',{})

    def scope(self, inventory):
        if self.inventory and self.inventory!=inventory:
            raise AppError(409,'AI_V2_SOURCE_CHANGED','Document inventory/revision changed; start a new task.')
        self.inventory=copy.deepcopy(inventory)
        self.whole=True

    def observed(self, passage):
        identity=passage['chunk_id']
        expected=self.inventory.get(identity)
        if expected and ((expected.get('content_sha256') is not None and expected['content_sha256']!=digest(passage['text'])) or
                expected['revision']!=passage['revision'] or expected['location']!=passage['location']):
            raise AppError(409,'AI_V2_SOURCE_CHANGED','Source changed during reading.')
        self.read.add(identity)

    def register(self, values, context):
        if not isinstance(values,list) or not 1<=len(values)<=64:
            raise AppError(422,'AI_V2_OBLIGATIONS','Invalid child obligation list.')
        for child in values:
            if (not isinstance(child,dict) or set(child)-{'id','objective','source_ids','administrative'}
                    or not isinstance(child.get('id'),str) or not 1<=len(child['id'])<=80
                    or not isinstance(child.get('objective'),str) or not 1<=len(child['objective'])<=2000
                    or not isinstance(child.get('source_ids'),list) or not child['source_ids']
                    or any(not isinstance(i,str) or i not in context for i in child['source_ids'])):
                raise AppError(422,'AI_V2_OBLIGATIONS','Child obligations require retrieved evidence.')
            identity=child['id']
            item={'id':identity,'objective':child['objective'],'administrative':child.get('administrative') is True,
                'provenance':[{'chunk_id':i,'document_id':context[i]['document_id'],
                    'revision':context[i]['revision'],'location':context[i]['location'],
                    'content_sha256':digest(context[i]['text'])} for i in dict.fromkeys(child['source_ids'])]}
            if identity in self.obligations and self.obligations[identity]!=item:
                raise AppError(409,'AI_V2_OBLIGATION_CONFLICT','Existing child identity cannot change silently.')
            self.obligations[identity]=item

    def progress(self, accepted_children=()):
        mapped={p['chunk_id'] for c in self.obligations.values() for p in c['provenance']}
        required={i for i,c in self.obligations.items() if not c['administrative']}
        return {'whole_scope':self.whole,'inventory_segments':len(self.inventory),
            'read_segments':len(set(self.inventory)&self.read),
            'unread_source_ids':sorted(set(self.inventory)-self.read),
            'unanalyzed_source_ids':sorted(set(self.inventory)-mapped),
            'missing_child_ids':sorted(required-set(accepted_children)),
            'missing_physical_pages':{d:p['missing_indexed_pages'] for d,p in self.physical_inventory.items() if p['missing_indexed_pages']},
            'unsupported_original_scopes':self.unsupported_scopes,
            'semantic_completeness':'not_proven_by_inventory_counts'}

    def snapshot(self):
        return {'inventory':self.inventory,'obligations':self.obligations,'read':sorted(self.read),'whole':self.whole,
            'physical_inventory':self.physical_inventory,'unsupported_original_scopes':self.unsupported_scopes}
