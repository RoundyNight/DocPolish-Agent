# spec_store.py -- 规范驱动的样式规范：通用 schema + 激活机制
#
# 设计原则：引擎不假定任何领域（学术论文 / 商业合同 / 报告 ……）。
# 每份规范自带角色集（roles 的 key 由各 spec 自定义），工具只按 role 查表落样式。
# 学术论文只是其中一份 preset 数据，不是引擎的导向。

import os
import sqlite3
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ParaStyle(BaseModel):
    """单一段落样式。值已规范化：字号为 pt，缩进为 'Nch'/'Ncm' 字符串。"""
    model_config = ConfigDict(extra="ignore")

    font_east_asia: str | None = None
    font_ascii: str | None = None
    font_size: float | None = None
    bold: bool | None = None
    color: str | None = None
    alignment: str | None = None
    first_line_indent: str | None = None
    left_indent: str | None = None
    line_spacing: float | None = None
    space_before: float | None = None
    space_after: float | None = None


class CompositeStyle(BaseModel):
    """复合段落"引题/内容"切分规则（如 摘要：xxx、第一条：xxx）。通用，不限定领域。"""
    model_config = ConfigDict(extra="ignore")

    label_pattern: str
    label_style: ParaStyle
    content_style: ParaStyle


class RoleDef(BaseModel):
    """单个角色的定义：可读名 + LLM 识别线索 + 目标样式（可选复合切分）。"""
    label: str
    hints: str
    style: ParaStyle
    composite: CompositeStyle | None = None


class StyleSpec(BaseModel):
    """一份完整排版规范。roles 的 key 由各 spec 自定义，引擎不假定固定角色集。"""
    id: str
    domain: str
    roles: dict[str, RoleDef]
    page_setup: dict = Field(default_factory=dict)
    source: Literal["preset", "template", "regulation"] = "preset"


# ---------- S2: 模板逆向的结构化输出 schema (LLM 角色分类) ----------
class ParagraphRoleAssignment(BaseModel):
    """单段落的角色判定。"""
    index: int
    role: str
    confidence: float = 1.0
    reason: str = ""


class RoleClassificationResult(BaseModel):
    """整篇模板的角色分类结果 (LLM with_structured_output 产物)。"""
    assignments: list[ParagraphRoleAssignment]
    roles_discovered: list[str]


class StyleExtraction(BaseModel):
    """服务端按 role 聚合的样式众数 (中间产物, 不直接给用户)。"""
    role: str
    mode_style: ParaStyle
    composite: CompositeStyle | None = None
    sample_indices: list[int] = Field(default_factory=list)
    sample_texts: list[str] = Field(default_factory=list)
    count: int = 0
    anomalies: list[dict] = Field(default_factory=list)


# ---------- 预置规范注册（懒加载，避免导入期副作用） ----------
_PRESETS: dict[str, StyleSpec] = {}
_DYNAMIC_SPECS: dict[str, StyleSpec] = {}
_STORE_PATH = os.path.join(os.path.dirname(__file__), "workspace", "style_specs.sqlite3")
_store_lock = threading.Lock()


def _connect_store() -> sqlite3.Connection:
    """打开动态规范持久化库；SQLite 同时兼容重启恢复和多进程读取。"""
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    conn = sqlite3.connect(_STORE_PATH, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS dynamic_specs "
        "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
    )
    return conn


def _load_dynamic_spec(spec_id: str) -> StyleSpec | None:
    with _store_lock, _connect_store() as conn:
        row = conn.execute(
            "SELECT payload FROM dynamic_specs WHERE id = ?", (spec_id,)
        ).fetchone()
    if row is None:
        return None
    spec = StyleSpec.model_validate_json(row[0])
    _DYNAMIC_SPECS[spec.id] = spec
    return spec


def _list_dynamic_ids() -> list[str]:
    with _store_lock, _connect_store() as conn:
        rows = conn.execute("SELECT id FROM dynamic_specs ORDER BY rowid").fetchall()
    return [row[0] for row in rows]


def _register_presets() -> None:
    from specs.general import build_general_spec
    from specs.academic_gb import build_academic_gb_spec

    _PRESETS.clear()
    for builder in (build_general_spec, build_academic_gb_spec):
        spec = builder()
        _PRESETS[spec.id] = spec


def list_presets() -> list[str]:
    if not _PRESETS:
        _register_presets()
    return list(_PRESETS.keys())


def get_preset(spec_id: str) -> StyleSpec | None:
    if not _PRESETS:
        _register_presets()
    return _PRESETS.get(spec_id)


def register_dynamic_spec(spec: StyleSpec, persist: bool = False) -> None:
    """注册运行时 spec；仅在用户明确选择保存时写入持久化库。"""
    _DYNAMIC_SPECS[spec.id] = spec
    if not persist:
        return
    with _store_lock, _connect_store() as conn:
        conn.execute(
            "INSERT INTO dynamic_specs(id, payload) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
            (spec.id, spec.model_dump_json()),
        )


def get_spec(spec_id: str) -> StyleSpec | None:
    """统一查询: 预置 + 动态。"""
    spec = get_preset(spec_id)
    if spec is not None:
        return spec
    cached = _DYNAMIC_SPECS.get(spec_id)
    return cached if cached is not None else _load_dynamic_spec(spec_id)


def list_all_specs() -> list[str]:
    if not _PRESETS:
        _register_presets()
    dynamic_ids = _list_dynamic_ids()
    return list(_PRESETS.keys()) + dynamic_ids


def delete_dynamic_spec(spec_id: str) -> bool:
    """删除临时或已保存的动态模板；预置规范不允许删除。"""
    if get_preset(spec_id) is not None:
        raise ValueError("系统预置规范不能删除")
    memory_existed = _DYNAMIC_SPECS.pop(spec_id, None) is not None
    with _store_lock, _connect_store() as conn:
        cursor = conn.execute("DELETE FROM dynamic_specs WHERE id = ?", (spec_id,))
    deleted = memory_existed or cursor.rowcount > 0

    global _active_spec
    with _active_lock:
        if _active_spec is not None and _active_spec.id == spec_id:
            _active_spec = None
    return deleted


# ---------- 激活机制（单用户；多用户需 contextvars，已知限制） ----------
_active_spec: StyleSpec | None = None
_active_lock = threading.Lock()


def set_active_spec(spec_id: str) -> StyleSpec:
    spec = get_spec(spec_id)
    if spec is None:
        raise KeyError(f"未知的排版规范 id: {spec_id}; 可用: {list_all_specs()}")
    global _active_spec
    with _active_lock:
        _active_spec = spec
    return spec


def get_active_spec() -> StyleSpec | None:
    return _active_spec
