import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import spec_store
from agent import analyze_document, route_after_analysis
from main import AgentStartRequest
from spec_store import ParaStyle, RoleDef, StyleSpec


def _template_spec(spec_id: str = "template_test") -> StyleSpec:
    return StyleSpec(
        id=spec_id,
        domain="测试模板",
        source="template",
        roles={
            "body": RoleDef(
                label="正文",
                hints="普通正文段落",
                style=ParaStyle(font_east_asia="宋体", font_size=12),
            )
        },
    )


class TemplateSpecRegressionTests(unittest.TestCase):
    def test_agent_start_accepts_v2_dehydrated_object(self):
        request = AgentStartRequest(
            api_key="test-key",
            model="deepseek-chat",
            dehydrated_data={
                "baseline": {},
                "page_setup": {},
                "paragraphs": [],
                "tables": [],
            },
            message="按模板排版",
            spec_id="general",
        )
        self.assertEqual(request.dehydrated_data["paragraphs"], [])

    def test_dynamic_spec_survives_memory_cache_clear(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = str(Path(tmp_dir) / "specs.sqlite3")
            with patch.object(spec_store, "_STORE_PATH", store_path):
                spec_store._DYNAMIC_SPECS.clear()
                expected = _template_spec()
                spec_store.register_dynamic_spec(expected, persist=True)
                spec_store._DYNAMIC_SPECS.clear()  # 模拟后端重启后的空内存

                restored = spec_store.get_spec(expected.id)
                self.assertIsNotNone(restored)
                self.assertEqual(restored.model_dump(), expected.model_dump())
                self.assertIn(expected.id, spec_store.list_all_specs())
        spec_store._DYNAMIC_SPECS.clear()

    def test_unsaved_dynamic_spec_is_memory_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = str(Path(tmp_dir) / "specs.sqlite3")
            with patch.object(spec_store, "_STORE_PATH", store_path):
                spec_store._DYNAMIC_SPECS.clear()
                temporary = _template_spec("template_temporary")
                spec_store.register_dynamic_spec(temporary)

                self.assertIsNotNone(spec_store.get_spec(temporary.id))
                self.assertNotIn(temporary.id, spec_store.list_all_specs())
                spec_store._DYNAMIC_SPECS.clear()
                self.assertIsNone(spec_store.get_spec(temporary.id))

    def test_saved_template_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store_path = str(Path(tmp_dir) / "specs.sqlite3")
            with patch.object(spec_store, "_STORE_PATH", store_path):
                spec_store._DYNAMIC_SPECS.clear()
                saved = _template_spec("template_delete_me")
                spec_store.register_dynamic_spec(saved, persist=True)

                self.assertTrue(spec_store.delete_dynamic_spec(saved.id))
                self.assertNotIn(saved.id, spec_store.list_all_specs())
                self.assertIsNone(spec_store.get_spec(saved.id))

    def test_preset_template_cannot_be_deleted(self):
        with self.assertRaisesRegex(ValueError, "预置规范不能删除"):
            spec_store.delete_dynamic_spec("general")

    def test_missing_spec_stops_before_planning(self):
        result = asyncio.run(
            analyze_document(
                {
                    "doc_path": "",
                    "spec_id": "template_missing",
                    "user_message": "按模板排版",
                }
            )
        )
        self.assertEqual(result["current_step"], "error")
        self.assertIn("template_missing", result["error"])
        self.assertEqual(route_after_analysis(result), "end")


if __name__ == "__main__":
    unittest.main()
