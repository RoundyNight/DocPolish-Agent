# dehydrator.py v2 -- 文档脱水(共用底座): 服务"目标文档规划"与"模板规范提取"两个场景
#
# 设计要点:
# 1. 双格式输入: 兼容 OfficeCLI 嵌套输出(data.results[0].children + effective.*)与
#    扁平输出(data:[{index, font-eastasia, size, ...}])。
# 2. 统一命名: 所有样式属性归一化为 snake_case, 与 doc_tools/ParaStyle 字段对齐,
#    确保脱水 JSON 保留 12 个工具对应的格式信息类别。
# 3. 基线 + 差异压缩: 输出含 baseline(文档最常见样式) + page_setup + paragraphs,
#    每段仅保留相对基线的差异(角色判别信号); 精确样式值由服务端从原始 JSON 取(不进 LLM)。
# 4. 不假定领域: 既可用于目标文档(Planner 判角色), 也可用于模板(逆向出 StyleSpec)。
import json
from collections import Counter

# ---------- 属性映射: 输入键(嵌套 effective.* 或 扁平 kebab-case) -> 归一化 snake_case ----------
# 覆盖 12 个工具能操作的全部样式类别
_STYLE_KEY_MAP = {
    # 嵌套 effective.*
    "effective.font.eastAsia": "font_east_asia",
    "effective.font.ascii": "font_ascii",
    "effective.size": "font_size",
    "effective.bold": "bold",
    "effective.color": "color",
    "effective.alignment": "alignment",
    "effective.indent.firstLine": "first_line_indent",
    "effective.indent.left": "left_indent",
    "effective.indent.right": "right_indent",
    "effective.lineSpacing": "line_spacing",
    "effective.spaceBefore": "space_before",
    "effective.spaceAfter": "space_after",
    "effective.pageBreakBefore": "page_break_before",
    # 扁平 kebab-case
    "font-eastasia": "font_east_asia",
    "font-ascii": "font_ascii",
    "size": "font_size",
    "bold": "bold",
    "color": "color",
    "alignment": "alignment",
    "indent-firstline": "first_line_indent",
    "indent-left": "left_indent",
    "indent-right": "right_indent",
    "line-spacing": "line_spacing",
    "spacing-before": "space_before",
    "spacing-after": "space_after",
    "page-break-before": "page_break_before",
}

# 参与基线统计的属性(角色判别性强的)
_BASELINE_SNS = (
    "font_east_asia", "font_ascii", "font_size", "bold", "color",
    "alignment", "line_spacing", "space_before", "space_after",
    "first_line_indent", "style",
)

# 全部工具可操作属性(用于差异保留)
_STYLE_SNS = tuple(set(_STYLE_KEY_MAP.values()))

_SECTION_MAP = {
    "pageWidth": "page_width", "pageHeight": "page_height",
    "marginTop": "margin_top", "marginBottom": "margin_bottom",
    "marginLeft": "margin_left", "marginRight": "margin_right",
}

_TEXT_TRUNC = 30
_RUN_TEXT_TRUNC = 20


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in ("true", "1", "yes", "on")


# ---------- 节点提取(双格式) ----------
def _extract_nodes(raw_json):
    """返回 (paragraphs, tables, section)。兼容嵌套与扁平格式。"""
    if not isinstance(raw_json, dict):
        return [], [], None
    data = raw_json.get("data")

    # 嵌套: data 是 dict 且含 results
    if isinstance(data, dict) and ("results" in data or "matches" in data):
        return _from_nested_children(data.get("results", []))
    # 扁平: data 是段落列表
    if isinstance(data, list):
        paras = [n for n in data if isinstance(n, dict) and "text" in n]
        return paras, [], None
    # raw_json 本身即嵌套 data dict
    if "results" in raw_json:
        return _from_nested_children(raw_json.get("results", []))
    return [], [], None


def _from_nested_children(results):
    children = results[0].get("children", []) if results else []
    paras = [n for n in children if isinstance(n, dict) and n.get("type") == "paragraph"]
    tables = [n for n in children if isinstance(n, dict) and n.get("type") == "table"]
    section = next((n for n in children if isinstance(n, dict) and n.get("type") == "section"), None)
    return paras, tables, section


def _iter_all_para_nodes(nodes):
    """从 children 列表递归 yield 所有段落节点(含表格内段落, 用于基线统计)。"""
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type")
        if t == "paragraph":
            yield n
        elif t == "table":
            yield from _iter_table_paras(n)
        elif t is None and "text" in n:  # 扁平格式段落
            yield n


def _iter_table_paras(table):
    for row in table.get("children", []):
        if row.get("type") != "row":
            continue
        for cell in row.get("children", []):
            if cell.get("type") != "cell":
                continue
            for c in cell.get("children", []):
                if c.get("type") == "paragraph":
                    yield c


# ---------- 样式读取 ----------
def _read_style(node) -> dict:
    """从段落/run 节点读取归一化样式(合并 format 与节点级属性)。"""
    if not isinstance(node, dict):
        return {}
    out = {}
    sources = {}
    fmt = node.get("format")
    if isinstance(fmt, dict):
        sources.update(fmt)
    sources.update(node)
    for k, v in sources.items():
        sn = _STYLE_KEY_MAP.get(k)
        if sn and v is not None:
            out[sn] = v
    style = node.get("style") or (fmt.get("styleName") if isinstance(fmt, dict) else None)
    if style and style not in ("Normal", "Normal (Web)", ""):
        out["style"] = style
    return out


def _read_runs(node, para_style: dict) -> list:
    """读取 run 级差异(与段落样式对比, 仅保留差异, 供 format_runs 识别复合段落)。"""
    raw_runs = []
    children = node.get("children")
    if isinstance(children, list):
        raw_runs = [c for c in children if isinstance(c, dict) and c.get("type") == "run"]
    elif isinstance(node.get("runs"), list):
        raw_runs = node["runs"]
    runs_out = []
    for r in raw_runs:
        rtext = (r.get("text") or "").strip()
        if not rtext:
            continue
        rstyle = _read_style(r)
        diff = {k: v for k, v in rstyle.items() if para_style.get(k) != v}
        if diff:
            disp = rtext[:_RUN_TEXT_TRUNC] + ("..." if len(rtext) > _RUN_TEXT_TRUNC else "")
            entry = {"text": disp}
            entry.update(diff)
            runs_out.append(entry)
    return runs_out


def _read_section_page_setup(section) -> dict:
    if not isinstance(section, dict):
        return {}
    fmt = section.get("format") or {}
    out = {}
    for k, sn in _SECTION_MAP.items():
        v = fmt.get(k)
        if v is not None:
            out[sn] = v
    return out


# ---------- 基线 ----------
def calculate_dynamic_baseline(nodes) -> dict:
    """统计文档中最常见样式值作为基准线(归一化 snake_case)。
    nodes 可为 children 列表(含 section/table/paragraph)或纯段落列表。兼容嵌套与扁平。"""
    counters = {sn: Counter() for sn in _BASELINE_SNS}
    for p in _iter_all_para_nodes(nodes):
        st = _read_style(p)
        for sn in _BASELINE_SNS:
            if sn in st and st[sn] is not None:
                counters[sn][str(st[sn])] = st[sn]
    baseline = {}
    for sn, c in counters.items():
        if c:
            baseline[sn] = c[c.most_common(1)[0][0]]
    return baseline


# ---------- 段落/表格处理 ----------
def _process_paragraph(node, baseline: dict, index: int) -> dict:
    text = (node.get("text") or "").strip()
    if not text:
        return {"index": index, "text": ""}
    disp = text[:_TEXT_TRUNC] + ("..." if len(text) > _TEXT_TRUNC else "")
    st = _read_style(node)
    out = {"index": index, "text": disp}
    for sn in _STYLE_SNS:
        if sn not in st:
            continue
        if sn == "page_break_before":
            if _truthy(st[sn]):
                out[sn] = True
            continue
        if st[sn] != baseline.get(sn):
            out[sn] = st[sn]
    # Word 命名样式(最强角色信号, 映射 apply_named_style): 与基线不同则保留
    if st.get("style") and st["style"] != baseline.get("style"):
        out["style"] = st["style"]
    runs = _read_runs(node, st)
    if runs:
        out["runs"] = runs
    return out


def _process_table(node, baseline: dict) -> dict:
    table_node = {"type": "table", "path": node.get("path", ""), "rows": []}
    for row in node.get("children", []):
        if row.get("type") != "row":
            continue
        row_data = {"cells": []}
        for cell in row.get("children", []):
            if cell.get("type") != "cell":
                continue
            cell_data = {"paragraphs": []}
            for c in cell.get("children", []):
                if c.get("type") == "paragraph":
                    cell_data["paragraphs"].append(_process_paragraph(c, baseline, -1))
            row_data["cells"].append(cell_data)
        table_node["rows"].append(row_data)
    return table_node


# ---------- 主函数 ----------
def dehydrate_document(raw_json) -> dict:
    """脱水文档, 返回 {baseline, page_setup, paragraphs, tables}。
    paragraphs 仅含正文级段落(带全局 index); tables 标注结构(无 index, 当前工具不支持改表格内段落)。"""
    paras, tables, section = _extract_nodes(raw_json)
    if not paras and not tables:
        return {"baseline": {}, "page_setup": {}, "paragraphs": [], "tables": []}

    # 基线统计覆盖正文 + 表格内段落
    baseline_nodes = list(paras) + list(tables)
    baseline = calculate_dynamic_baseline(baseline_nodes)
    page_setup = _read_section_page_setup(section)

    para_out = []
    for idx, n in enumerate(paras):
        para_out.append(_process_paragraph(n, baseline, idx))

    table_out = [_process_table(t, baseline) for t in tables]
    return {
        "baseline": baseline,
        "page_setup": page_setup,
        "paragraphs": para_out,
        "tables": table_out,
    }
