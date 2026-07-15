# template_ingest.py -- S2 模板逆向管线: LLM role 分类 + 服务端样式众数抽取 → StyleSpec
#
# 两层分工:
#   LLM 层(轻): 看压缩 JSON 判 role → RoleClassificationResult
#   服务端层(重, 不进 LLM): 回 raw JSON 取全量样式 → 众数 → ParaStyle → StyleSpec
# officecli 仅做基础设施(解析), 智能环节全自研。
import json
import re
import uuid
from collections import Counter
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from spec_store import (
    StyleSpec, RoleDef, ParaStyle, CompositeStyle,
    RoleClassificationResult, ParagraphRoleAssignment, StyleExtraction,
    register_dynamic_spec,
)
from dehydrator import _extract_nodes, _read_style

# ---------- 通用角色词表(建议 LLM 优先使用, 但不强制) ----------
_SUGGESTED_ROLES = [
    "cover_title", "title", "h1", "h2", "h3", "h4",
    "body", "caption", "quote", "reference", "list_item",
    "abstract", "keyword", "acknowledgment",
    "header", "footer", "note",
]

_CLASSIFY_SYSTEM = """你是文档结构分析专家。给定一份已排版文档的压缩 JSON, 为每个段落判定语义角色。

输入格式: paragraphs 数组, 每段含:
- index: 段落索引
- text: 截断文本(≤30字)
- style: Word 命名样式(如有, 如 Title/heading 1/Body Text)
- 其余字段: 相对文档基准线的样式差异信号(如 font_size/bold/alignment, 仅在偏离基准时出现)

判定原则:
1. 样式差异是强信号: 字号明显大于基准 + 加粗 → 标题类; 居中 + 最大字号 → 题名/封面
2. Word 样式名是强线索: Title → cover_title/title; heading 1/2/3 → h1/h2/h3; Body Text → body
3. 文本特征: "图N""表N" 开头 → caption; 编号 1./2.1 → h1/h2; "摘要""关键词" → abstract/keyword
4. 优先使用建议角色名: """ + ", ".join(_SUGGESTED_ROLES) + """
5. 遇到建议词表未覆盖的角色可自行命名(snake_case, 语义化)
6. 为每段给出 confidence(0-1) 和简短 reason

你必须为每一个 index 给出判定, 不允许遗漏。

输出格式(严格): 一个 JSON 对象, 包含 assignments 数组和 roles_discovered 数组:
{
  "assignments": [
    {"index": 0, "role": "cover_title", "confidence": 0.95, "reason": "居中最大字号"},
    {"index": 1, "role": "body", "confidence": 0.9, "reason": "正文"}
  ],
  "roles_discovered": ["cover_title", "body"]
}
禁止输出裸数组, 必须是上述对象结构。"""


# ---------- 样式值归一化: _read_style 原始值 → ParaStyle 兼容类型 ----------
def _normalize_value(key: str, val) -> object:
    s = str(val).strip()
    if not s:
        return None
    if key == "font_size":
        return float(s.replace("pt", ""))
    if key == "bold":
        return s.lower() in ("true", "1", "yes")
    if key == "line_spacing":
        if s.endswith("x"):
            return float(s[:-1])
        return None  # pt-based 固定行距暂不支持(少数情况), 跳过
    if key in ("space_before", "space_after"):
        try:
            return float(s.replace("pt", ""))
        except ValueError:
            return None
    if key in ("first_line_indent", "left_indent", "right_indent"):
        if re.match(r"^[\d.]+(cm|in|pt|ch)$", s):
            return s
        try:  # 纯数字视为 twips → cm (1cm = 567 twips)
            return f"{float(s) / 567:.2f}cm"
        except ValueError:
            return None
    return s  # font_east_asia, font_ascii, color, alignment


def _normalize_styles(raw: dict) -> dict:
    """将 _read_style 输出归一化为 ParaStyle 兼容 dict (剔除 style 字段)。"""
    out = {}
    for k, v in raw.items():
        if k == "style":
            continue
        nv = _normalize_value(k, v)
        if nv is not None:
            out[k] = nv
    return out


# ---------- LLM 层: role 分类 ----------
def classify_roles(dehydrated: dict, api_key: str, model: str) -> RoleClassificationResult:
    """LLM 判定每段 role, 返回 RoleClassificationResult。
    用 JSON mode + 手动解析(与 plan_formatting 一致, with_structured_output 在 DeepSeek 上不稳)。"""
    paragraphs = dehydrated.get("paragraphs", []) if isinstance(dehydrated, dict) else []
    if not paragraphs:
        raise ValueError("脱水文档无段落, 无法分类")

    plan_model = "deepseek-chat" if model == "deepseek-reasoner" else model
    llm = ChatOpenAI(
        model=plan_model,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0.1,
        max_tokens=8192,
        model_kwargs={"response_format": {"type": "json_object"}},
    )

    user_content = (
        f"文档基准线: {json.dumps(dehydrated.get('baseline', {}), ensure_ascii=False)}\n\n"
        f"以下是文档段落(压缩, 仅保留相对基准线的样式差异):\n"
        f"```json\n{json.dumps(paragraphs, ensure_ascii=False)}\n```\n\n"
        f"请为每个 index 判定角色。"
    )
    messages = [SystemMessage(content=_CLASSIFY_SYSTEM), HumanMessage(content=user_content)]
    raw_response = llm.invoke(messages)
    content = raw_response.content or ""

    return _parse_classification(content)


def _parse_classification(content: str) -> RoleClassificationResult:
    """从 LLM JSON 输出解析 RoleClassificationResult, 容错裸数组/包装对象两种形态。"""
    data = json.loads(content)

    # 兼容两种形态: 裸数组 [{index,role,...}] 或 包装对象 {assignments:[...], roles_discovered:[...]}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "assignments" in data:
        items = data["assignments"]
    elif isinstance(data, dict) and "tool_calls" in data:
        # 极少情况: LLM 误用 plan_formatting 格式
        items = data["tool_calls"]
    else:
        raise ValueError(f"无法识别的 LLM 输出结构: 期望数组或含 assignments 的对象, 收到 keys={list(data.keys()) if isinstance(data, dict) else type(data)}")

    assignments = []
    for item in items:
        if not isinstance(item, dict) or "index" not in item or "role" not in item:
            continue
        assignments.append(ParagraphRoleAssignment(
            index=int(item["index"]),
            role=str(item["role"]),
            confidence=float(item.get("confidence", 1.0)),
            reason=str(item.get("reason", "")),
        ))
    if not assignments:
        raise ValueError("LLM 输出中无有效的角色判定(index/role 缺失)")

    roles_discovered = list(set(a.role for a in assignments))
    return RoleClassificationResult(assignments=assignments, roles_discovered=roles_discovered)


# ---------- 服务端层: 按 role 取样式众数 ----------
def _build_index_to_node_map(raw_json: dict) -> dict[int, dict]:
    """dehydrated index → raw 段落节点 (仅顶层 paragraph, 与 dehydrator index 分配一致)。"""
    paras, _tables, _section = _extract_nodes(raw_json)
    return {i: node for i, node in enumerate(paras)}


def _take_mode(values: list) -> object:
    """对离散样式值取众数 (样式是离散的, 不取均值)。"""
    if not values:
        return None
    c = Counter(str(v) for v in values)
    most_common_str = c.most_common(1)[0][0]
    for v in values:
        if str(v) == most_common_str:
            return v
    return None


def _detect_composite(raw_para_nodes: list[dict], dehydrated_paras: list[dict]) -> Optional[CompositeStyle]:
    """检测复合段落(引题/内容): 同 role 内多数段呈"短前缀+剩余"且 run 样式不同。"""
    # 收集有 runs 差异的段落
    split_samples = []  # (prefix_text, prefix_style, content_style)
    for dp in dehydrated_paras:
        runs = dp.get("runs")
        if not runs or len(runs) < 1:
            continue
        text = dp.get("text", "")
        # 检查是否有短前缀(以：或:结尾, ≤10字)
        m = re.match(r"^(.{1,10}[:：])", text)
        if not m:
            continue
        prefix = m.group(1)
        # 从 raw 节点取 run 样式
        idx = dp.get("index")
        raw_node = next((n for n in raw_para_nodes if _node_index(n) == idx), None)
        if raw_node is None:
            continue
        children = raw_node.get("children", [])
        raw_runs = [c for c in children if isinstance(c, dict) and c.get("type") == "run"]
        if len(raw_runs) < 2:
            continue
        prefix_style = _normalize_styles(_read_style(raw_runs[0]))
        content_style = _normalize_styles(_read_style(raw_runs[1])) if len(raw_runs) > 1 else {}
        if prefix_style and content_style:
            split_samples.append((prefix, prefix_style, content_style))

    if len(split_samples) < 1:
        return None
    # 取最常见的 prefix 模式
    prefix_counter = Counter(s[0] for s in split_samples)
    common_prefix = prefix_counter.most_common(1)[0][0]
    # 构造正则: 容许冒号前后空格
    escaped = re.escape(common_prefix).replace(re.escape("："), "[：:]").replace(re.escape(":"), "[：:]")
    pattern = f"^{escaped}\\s*"
    # label/content 样式取众数
    label_styles = [s[1] for s in split_samples]
    content_styles = [s[2] for s in split_samples]
    label_para = ParaStyle(**{k: _take_mode([d.get(k) for d in label_styles if d.get(k) is not None])
                              for k in set().union(*[d.keys() for d in label_styles]) if _take_mode([d.get(k) for d in label_styles if d.get(k) is not None]) is not None})
    content_para = ParaStyle(**{k: _take_mode([d.get(k) for d in content_styles if d.get(k) is not None])
                                for k in set().union(*[d.keys() for d in content_styles]) if _take_mode([d.get(k) for d in content_styles if d.get(k) is not None]) is not None})
    return CompositeStyle(label_pattern=pattern, label_style=label_para, content_style=content_para)


def _node_index(node: dict) -> int:
    """从 raw 节点取 index (扁平格式) 或 None (嵌套无 index, 用顺序)。"""
    return node.get("index", -1) if isinstance(node, dict) else -1


def extract_styles_by_role(raw_json: dict, classification: RoleClassificationResult,
                           dehydrated: dict) -> list[StyleExtraction]:
    """服务端: 按 role 分组 → 回 raw JSON 取全量样式 → 众数 → StyleExtraction 列表。"""
    index_map = _build_index_to_node_map(raw_json)
    dehydrated_paras = dehydrated.get("paragraphs", []) if isinstance(dehydrated, dict) else []

    # 按 role 分组 index
    role_indices: dict[str, list[int]] = {}
    role_confidence: dict[str, list[float]] = {}
    for a in classification.assignments:
        role_indices.setdefault(a.role, []).append(a.index)
        role_confidence.setdefault(a.role, []).append(a.confidence)

    extractions = []
    for role, indices in role_indices.items():
        # 收集该 role 的 raw 段落节点和归一化样式
        raw_nodes = [index_map[i] for i in indices if i in index_map]
        dparas = [dp for dp in dehydrated_paras if dp.get("index") in indices]
        all_styles = [_normalize_styles(_read_style(n)) for n in raw_nodes]
        all_styles = [s for s in all_styles if s]

        # 逐属性取众数
        all_keys = set().union(*[set(s.keys()) for s in all_styles]) if all_styles else set()
        mode_dict = {}
        for k in all_keys:
            vals = [s.get(k) for s in all_styles if s.get(k) is not None]
            mv = _take_mode(vals)
            if mv is not None:
                mode_dict[k] = mv
        mode_style = ParaStyle(**mode_dict)

        # 复合检测
        composite = _detect_composite(raw_nodes, dparas)

        # 异类检测: 偏离众数的段落
        anomalies = []
        for i, style in zip(indices, all_styles):
            for k, mv in mode_dict.items():
                if style.get(k) is not None and str(style.get(k)) != str(mv):
                    anomalies.append({"index": i, "attr": k, "expected": str(mv), "actual": str(style.get(k))})
                    break

        sample_indices = indices[:3]
        sample_texts = [dp.get("text", "") for dp in dparas[:3]]
        extractions.append(StyleExtraction(
            role=role, mode_style=mode_style, composite=composite,
            sample_indices=sample_indices, sample_texts=sample_texts,
            count=len(indices), anomalies=anomalies,
        ))
    return extractions


# ---------- 组装 StyleSpec ----------
def _auto_hints(role: str, sample_texts: list[str]) -> str:
    """从样本文本自动生成识别线索(规则, 省 token)。"""
    if not sample_texts:
        return role
    first = sample_texts[0]
    if re.match(r"^(图|表)\s*\d", first):
        return "以'图N''表N'开头的题注行"
    if re.match(r"^\d+(\.\d+)*\s", first):
        return "以编号(如 1 / 2.1 / 3.2.1)开头的标题"
    if re.match(r"^(摘\s*要|关键词|Abstract|Key\s*words?)\s*[:：]", first, re.I):
        return "以'摘要''关键词'等引题开头的段落"
    if re.match(r"^(参考|引用|文献)", first):
        return "参考文献相关段落"
    if len(first) > 20:
        return "普通正文段落"
    return f"角色 {role} 的段落"


def assemble_spec(classification: RoleClassificationResult, extractions: list[StyleExtraction],
                  page_setup: dict, domain: str = "上传模板") -> StyleSpec:
    """组装 StyleSpec 并动态注册。"""
    roles = {}
    for ext in extractions:
        roles[ext.role] = RoleDef(
            label=ext.role,
            hints=_auto_hints(ext.role, ext.sample_texts),
            style=ext.mode_style,
            composite=ext.composite,
        )
    spec = StyleSpec(
        id=f"template_{uuid.uuid4().hex[:8]}",
        domain=domain,
        roles=roles,
        page_setup=page_setup,
        source="template",
    )
    register_dynamic_spec(spec)
    return spec


# ---------- 编排: 完整管线 ----------
# 缓存: spec_id → (raw_json, classification, dehydrated), 供 confirm 修正时重跑 extract
_INGEST_CACHE: dict[str, tuple] = {}


def ingest_template(raw_json: dict, api_key: str, model: str,
                    domain: str = "上传模板") -> tuple[StyleSpec, dict]:
    """完整模板逆向: dehydrate → classify(LLM) → extract(众数) → assemble → (spec, preview)。"""
    from dehydrator import dehydrate_document
    dehydrated = dehydrate_document(raw_json)
    if not dehydrated.get("paragraphs"):
        raise ValueError("文档解析后无段落, 无法逆向")

    classification = classify_roles(dehydrated, api_key, model)
    extractions = extract_styles_by_role(raw_json, classification, dehydrated)
    spec = assemble_spec(classification, extractions, dehydrated.get("page_setup", {}), domain)

    # 缓存供 confirm 修正
    _INGEST_CACHE[spec.id] = (raw_json, classification, dehydrated)

    preview = _build_preview(spec, classification, extractions)
    return spec, preview


def reassemble_with_corrections(spec_id: str, corrections: dict[int, str]) -> tuple[StyleSpec, dict]:
    """用户修正后重跑 extract+assemble (不重跑 LLM, 省成本)。
    corrections: {index: new_role}"""
    cached = _INGEST_CACHE.get(spec_id)
    if cached is None:
        raise KeyError(f"找不到逆向缓存: {spec_id}; 可能需重新上传模板")
    raw_json, old_classification, dehydrated = cached

    # 应用修正到 classification
    corr_map = {int(k): v for k, v in corrections.items()}
    new_assignments = []
    for a in old_classification.assignments:
        if a.index in corr_map:
            new_assignments.append(ParagraphRoleAssignment(
                index=a.index, role=corr_map[a.index], confidence=1.0, reason="用户修正"))
        else:
            new_assignments.append(a)
    new_classification = RoleClassificationResult(
        assignments=new_assignments,
        roles_discovered=list(set(a.role for a in new_assignments)),
    )

    extractions = extract_styles_by_role(raw_json, new_classification, dehydrated)
    # 用原 domain, 保留原 spec_id
    from spec_store import get_spec
    old_spec = get_spec(spec_id)
    domain = old_spec.domain if old_spec else "上传模板"
    spec = assemble_spec(new_classification, extractions, dehydrated.get("page_setup", {}), domain)
    spec.id = spec_id  # 保持 id 一致
    register_dynamic_spec(spec)  # 覆盖内存中的临时规范
    _INGEST_CACHE[spec_id] = (raw_json, new_classification, dehydrated)

    preview = _build_preview(spec, new_classification, extractions)
    return spec, preview


def _build_preview(spec: StyleSpec, classification: RoleClassificationResult,
                   extractions: list[StyleExtraction]) -> dict:
    """构建用户可读的审核预览。"""
    preview_roles = {}
    for ext in extractions:
        preview_roles[ext.role] = {
            "style": ext.mode_style.model_dump(exclude_none=True),
            "composite": ext.composite.label_pattern if ext.composite else None,
            "samples": ext.sample_texts,
            "count": ext.count,
            "anomalies": ext.anomalies[:5],
        }
    low_conf = [a.index for a in classification.assignments if a.confidence < 0.6]
    return {
        "spec_id": spec.id,
        "domain": spec.domain,
        "roles": preview_roles,
        "low_confidence_indices": low_conf,
        "page_setup": spec.page_setup,
    }
