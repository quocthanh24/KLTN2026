import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

class IntegrationStructureTests(unittest.TestCase):
    def test_single_orchestrator_without_legacy_fallback(self):
        tree=ast.parse((ROOT/'backend/app/services/ai_conversation_service.py').read_text(encoding='utf8'))
        imports=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertNotIn('app.services.semantic_query_plan',imports)
        self.assertNotIn('app.services.general_tutor',imports)
        calls=[n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        self.assertEqual(calls.count('run_v2'),1)
        self.assertNotIn('resolve_query',calls)

    def test_source_compiles(self):
        for f in ['ai_chat_v2.py','ai_chat_v2_tools.py','ai_conversation_service.py']:
            with self.subTest(file=f):
                compile((ROOT/'backend/app/services'/f).read_text(encoding='utf-8-sig'),f,'exec')
