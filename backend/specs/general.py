# specs/general.py -- 通用文档规范（默认，领域无关）
# 仅作为通用 StyleSpec 的一份示例数据；角色集为通用文档角色，可按需扩展。
# 字号已换算为 pt：小二=18, 小三=15, 小四=12, 五号=10.5, 小五=9。

from spec_store import StyleSpec, RoleDef, ParaStyle


def build_general_spec() -> StyleSpec:
    return StyleSpec(
        id="general",
        domain="通用文档",
        source="preset",
        page_setup={},
        roles={
            "title": RoleDef(
                label="标题/题名",
                hints="文档主标题，通常居中且字号最大",
                style=ParaStyle(font_east_asia="黑体", font_size=18, alignment="center"),
            ),
            "h1": RoleDef(
                label="一级标题",
                hints="一级章节标题，如'1 ...''一、...'",
                style=ParaStyle(font_east_asia="黑体", font_size=15, alignment="left", first_line_indent="0ch"),
            ),
            "h2": RoleDef(
                label="二级标题",
                hints="二级标题，如'1.1 ...''（一）...'",
                style=ParaStyle(font_east_asia="黑体", font_size=13, alignment="left", first_line_indent="0ch"),
            ),
            "h3": RoleDef(
                label="三级标题",
                hints="三级标题，如'1.1.1 ...''1) ...'",
                style=ParaStyle(font_east_asia="黑体", font_size=10.5, alignment="left", first_line_indent="0ch"),
            ),
            "body": RoleDef(
                label="正文",
                hints="普通正文段落",
                style=ParaStyle(font_east_asia="宋体", font_size=10.5, alignment="justify", first_line_indent="2ch"),
            ),
            "caption": RoleDef(
                label="图表题注",
                hints="以'图N''表N'开头的题注行",
                style=ParaStyle(font_east_asia="黑体", font_size=9, alignment="center"),
            ),
            "quote": RoleDef(
                label="引文/引用块",
                hints="引文、引用块，通常与正文风格相区别",
                style=ParaStyle(font_east_asia="楷体", font_size=10.5, alignment="justify", first_line_indent="2ch"),
            ),
            "reference": RoleDef(
                label="参考文献条目",
                hints="参考文献/资料列表中的条目",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="left"),
            ),
            "list_item": RoleDef(
                label="列表项",
                hints="项目符号或编号列表的条目",
                style=ParaStyle(font_east_asia="宋体", font_size=10.5, alignment="left", left_indent="0.74cm"),
            ),
        },
    )
