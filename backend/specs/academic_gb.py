# specs/academic_gb.py -- 示例预置规范：学术论文（参考 GB/T 学位论文编写规则附录 B）
#
# 仅作为通用 StyleSpec 的一份示例数据，演示 schema 可承载领域规范。
# 引擎与工具不假定"学术论文"领域；新增商业合同/报告等规范只是再加一份同类数据。
# 字号已换算为 pt：小二=18, 四号=14, 小四=12, 五号=10.5, 小五=9。

from spec_store import StyleSpec, RoleDef, ParaStyle, CompositeStyle


def build_academic_gb_spec() -> StyleSpec:
    return StyleSpec(
        id="academic_gb",
        domain="学术论文",
        source="preset",
        page_setup={},  # P1 填 A4/页边距
        roles={
            # ---------- 前置部分 ----------
            "cover_title_cn": RoleDef(
                label="中文题名",
                hints="前置部分首段、篇幅最大的中文标题，通常居中",
                style=ParaStyle(font_east_asia="黑体", font_size=18, alignment="center"),
            ),
            "author_cn": RoleDef(
                label="作者姓名(中文)",
                hints="题名下方的中文作者姓名",
                style=ParaStyle(font_east_asia="楷体", font_size=12, alignment="center"),
            ),
            "affil_cn": RoleDef(
                label="工作单位及通信方式(中文)",
                hints="作者下方的工作单位/邮箱/地址等中文行",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="center"),
            ),
            "abstract_cn": RoleDef(
                label="中文摘要",
                hints="以'摘要：'或'摘要:'开头的段落",
                style=ParaStyle(font_size=9, alignment="justify"),
                composite=CompositeStyle(
                    label_pattern=r"^摘\s*要\s*[：:]\s*",
                    label_style=ParaStyle(font_east_asia="黑体", font_size=9),
                    content_style=ParaStyle(font_east_asia="仿宋", font_size=9),
                ),
            ),
            "keyword_cn": RoleDef(
                label="中文关键词",
                hints="以'关键词：'开头的段落",
                style=ParaStyle(font_size=9, alignment="justify"),
                composite=CompositeStyle(
                    label_pattern=r"^关\s*键\s*词\s*[：:]\s*",
                    label_style=ParaStyle(font_east_asia="黑体", font_size=9),
                    content_style=ParaStyle(font_east_asia="仿宋", font_size=9),
                ),
            ),
            "title_en": RoleDef(
                label="英文题名",
                hints="前置部分的英文标题",
                style=ParaStyle(font_east_asia="黑体", font_size=14, alignment="center"),
            ),
            "author_en": RoleDef(
                label="英文作者姓名",
                hints="英文题名下方的作者姓名",
                style=ParaStyle(font_east_asia="宋体", font_size=10.5, alignment="center"),
            ),
            "affil_en": RoleDef(
                label="工作单位(英文)",
                hints="英文作者下方的工作单位等",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="center"),
            ),
            "abstract_en": RoleDef(
                label="英文摘要",
                hints="以'Abstract:'或'Abstract：'开头的段落",
                style=ParaStyle(font_size=9, alignment="justify"),
                composite=CompositeStyle(
                    label_pattern=r"^Abstract\s*[：:]\s*",
                    label_style=ParaStyle(font_east_asia="黑体", font_size=9),
                    content_style=ParaStyle(font_east_asia="宋体", font_size=9),
                ),
            ),
            "keyword_en": RoleDef(
                label="英文关键词",
                hints="以'Key words:'或'Keywords:'开头的段落",
                style=ParaStyle(font_size=9, alignment="justify"),
                composite=CompositeStyle(
                    label_pattern=r"^Key\s*words?\s*[：:]\s*",
                    label_style=ParaStyle(font_east_asia="黑体", font_size=9),
                    content_style=ParaStyle(font_east_asia="宋体", font_size=9),
                ),
            ),
            "other_front": RoleDef(
                label="其他前置项目",
                hints="前置部分中不属于题名/作者/单位/摘要/关键词的其它行",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="left"),
            ),
            # ---------- 正文部分 ----------
            "chapter_title": RoleDef(
                label="章编号与标题",
                hints="一级章节，如'1 引言''2 方法'，编号顶格",
                style=ParaStyle(font_east_asia="黑体", font_size=12, alignment="left", first_line_indent="0ch"),
            ),
            "section_title": RoleDef(
                label="节编号与标题",
                hints="二/三级章节，如'2.1''3.2.1'，编号顶格",
                style=ParaStyle(font_east_asia="黑体", font_size=10.5, alignment="left", first_line_indent="0ch"),
            ),
            "body": RoleDef(
                label="正文内容",
                hints="主体段落，非标题/非题注/非参考文献",
                style=ParaStyle(font_east_asia="宋体", font_size=10.5, alignment="justify", first_line_indent="2ch"),
            ),
            "caption": RoleDef(
                label="插图/表格编号与标题",
                hints="以'图N''表N'开头的题注行",
                style=ParaStyle(font_east_asia="黑体", font_size=9, alignment="center"),
            ),
            "table_content": RoleDef(
                label="表格内容/表注/图注",
                hints="表格内文字或图表注释",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="left"),
            ),
            "ack_title": RoleDef(
                label="致谢引题",
                hints="以'致谢'为标题的段落",
                style=ParaStyle(font_east_asia="黑体", font_size=10.5, alignment="left", first_line_indent="0ch"),
            ),
            "ack_body": RoleDef(
                label="致谢内容",
                hints="致谢标题下的正文",
                style=ParaStyle(font_east_asia="楷体", font_size=10.5, alignment="justify", first_line_indent="2ch"),
            ),
            "ref_title": RoleDef(
                label="参考文献引题(及章编号)",
                hints="以'参考文献'为标题的段落",
                style=ParaStyle(font_east_asia="黑体", font_size=12, alignment="left", first_line_indent="0ch"),
            ),
            "ref_body": RoleDef(
                label="参考文献条目",
                hints="参考文献列表中的各条目",
                style=ParaStyle(font_east_asia="宋体", font_size=9, alignment="left"),
            ),
            # ---------- 附录部分 ----------
            "appendix_title": RoleDef(
                label="附录编号与标题",
                hints="以'附录A''附录B'等开头的标题",
                style=ParaStyle(font_east_asia="黑体", font_size=12, alignment="left", first_line_indent="0ch"),
            ),
            "appendix_body": RoleDef(
                label="附录内容",
                hints="附录标题下的正文",
                style=ParaStyle(font_east_asia="宋体", font_size=10.5, alignment="justify", first_line_indent="2ch"),
            ),
        },
    )
