#!/usr/bin/env python3
"""Create a presentation-ready revision of the submitted course PPT.

The original PPTX is treated as a visual reference and remains untouched.  The
revision reuses its illustrated paper background, replaces template-only
divider slides with substantive content, and adds implementation, schedule,
risk, and evidence slides for an 8–10 minute selection presentation.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


W = Inches(13.333)
H = Inches(7.5)
FONT = "微软雅黑"

FOREST = RGBColor(48, 91, 76)
DARK = RGBColor(39, 61, 53)
SAGE = RGBColor(125, 163, 137)
MINT = RGBColor(221, 235, 223)
LIGHT_MINT = RGBColor(238, 245, 238)
PAPER = RGBColor(250, 249, 244)
ORANGE = RGBColor(211, 127, 88)
GOLD = RGBColor(221, 172, 91)
WHITE = RGBColor(255, 255, 255)
MUTED = RGBColor(101, 120, 111)
RED = RGBColor(176, 86, 70)


def background_blob(source: Presentation) -> bytes:
    for slide in source.slides:
        for shape in slide.shapes:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and shape.image.size == (1650, 2931):
                return shape.image.blob
    raise RuntimeError("Could not locate the illustrated background in the source PPTX")


def set_shape_fill(shape, color, transparency=0):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    # python-pptx currently has no public transparency setter.  Keep the
    # parameter for call-site readability and deterministic opaque output.
    shape.line.color.rgb = color


def add_bg(slide, blob):
    pic = slide.shapes.add_picture(BytesIO(blob), Inches(2.9165), Inches(-2.9165), Inches(7.5), Inches(13.333))
    pic.rotation = 90
    pic.name = "背景（沿用原稿）"


def add_shape(slide, kind, x, y, w, h, fill=PAPER, line=SAGE, radius=True):
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.line.color.rgb = line
    shape.line.width = Pt(1.2)
    return shape


def add_text(
    slide,
    text,
    x,
    y,
    w,
    h,
    *,
    size=20,
    color=DARK,
    bold=False,
    align=PP_ALIGN.LEFT,
    valign=MSO_ANCHOR.TOP,
    margin=0.04,
    line_spacing=1.0,
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(margin)
    frame.margin_right = Inches(margin)
    frame.margin_top = Inches(margin)
    frame.margin_bottom = Inches(margin)
    frame.vertical_anchor = valign
    p = frame.paragraphs[0]
    p.text = text
    p.alignment = align
    p.line_spacing = line_spacing
    run = p.runs[0]
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box


def add_bullets(slide, items, x, y, w, h, *, size=16, color=DARK, spacing=6, bullet=True):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    frame.margin_left = Inches(0.02)
    frame.margin_right = Inches(0.02)
    frame.margin_top = 0
    frame.margin_bottom = 0
    for idx, item in enumerate(items):
        p = frame.paragraphs[0] if idx == 0 else frame.add_paragraph()
        p.text = ("• " if bullet else "") + item
        p.space_after = Pt(spacing)
        p.line_spacing = 1.0
        r = p.runs[0]
        r.font.name = FONT
        r.font.size = Pt(size)
        r.font.color.rgb = color
    return box


def add_header(slide, number, title, subtitle=""):
    add_text(slide, f"{number:02d}", 1.23, 0.58, 0.55, 0.34, size=13, color=ORANGE, bold=True)
    add_text(slide, title, 1.78, 0.43, 9.8, 0.55, size=25, color=FOREST, bold=True)
    if subtitle:
        add_text(slide, subtitle, 1.8, 0.94, 9.6, 0.3, size=10, color=MUTED)
    line = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(1.23), Inches(1.25), Inches(10.85), Inches(0.035))
    set_shape_fill(line, SAGE)


def add_footer(slide, number, status="选题方案 · 尚待实施"):
    add_text(slide, status, 1.28, 6.86, 3.5, 0.22, size=9, color=MUTED)
    add_text(slide, str(number), 11.55, 6.85, 0.42, 0.22, size=9, color=MUTED, align=PP_ALIGN.RIGHT)


def add_pill(slide, text, x, y, w, *, fill=MINT, color=FOREST, size=13):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, 0.42, fill=fill, line=fill)
    add_text(slide, text, x + 0.06, y + 0.075, w - 0.12, 0.24, size=size, color=color, bold=True, align=PP_ALIGN.CENTER)


def add_card(slide, title, items, x, y, w, h, *, accent=FOREST, fill=PAPER, body_size=15):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill=fill, line=SAGE)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(0.09), Inches(h))
    set_shape_fill(bar, accent)
    add_text(slide, title, x + 0.27, y + 0.2, w - 0.48, 0.45, size=18, color=accent, bold=True)
    add_bullets(slide, items, x + 0.28, y + 0.82, w - 0.52, h - 1.0, size=body_size, color=DARK)


def add_notes(slide, text):
    try:
        slide.notes_slide.notes_text_frame.text = text
    except Exception:
        pass


def add_slide(prs, blob):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide, blob)
    return slide


def cover(prs, blob):
    slide = add_slide(prs, blob)
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 1.55, 1.42, 10.25, 4.5, fill=PAPER, line=SAGE)
    add_pill(slide, "大数据综合实践 · 选题确认汇报", 4.37, 1.83, 4.58, fill=MINT, size=14)
    add_text(slide, "基于 Spark 与 MongoDB 的", 2.0, 2.54, 9.35, 0.56, size=29, color=FOREST, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "多源技术内容知识挖掘与个性化发现 Web 系统", 1.86, 3.18, 9.65, 0.72, size=25, color=DARK, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "从“信息很多”走向“只发现值得我学的新知识”", 2.35, 4.17, 8.65, 0.45, size=17, color=ORANGE, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "组长：陶文杰　｜　组员：陈铭宇", 3.52, 5.05, 6.3, 0.35, size=15, color=MUTED, align=PP_ALIGN.CENTER)
    add_text(slide, "修订版 · 2026-09-04", 5.02, 6.45, 3.3, 0.25, size=10, color=MUTED, align=PP_ALIGN.CENTER)
    add_notes(slide, "建议用时 20 秒。只介绍选题和一句话价值。明确这是实施前的选题方案，不把目标写成已完成成果。")


def overview(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 1, "汇报路线", "FROM PROBLEM TO EXECUTION")
    items = [
        ("01", "问题与目标", "为什么需要个性化知识发现"),
        ("02", "数据与方法", "Stack Exchange + arXiv + Spark"),
        ("03", "实施方案", "MongoDB、API 与 Web 完整链路"),
        ("04", "三周计划", "先消除环境和数据风险"),
        ("05", "分工与证据", "两人各 50%，每层可答辩"),
    ]
    for idx, (num, title, body) in enumerate(items):
        y = 1.62 + idx * 0.95
        add_pill(slide, num, 1.55, y, 0.72, fill=FOREST, color=WHITE, size=12)
        add_text(slide, title, 2.55, y + 0.02, 2.25, 0.32, size=18, color=FOREST, bold=True)
        add_text(slide, body, 4.85, y + 0.04, 6.25, 0.28, size=14, color=MUTED)
    add_footer(slide, 1)
    add_notes(slide, "建议用时 20 秒。快速说明汇报不只讲选题，还会回答怎么做、三周怎么推进、怎么验收。")


def problem(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 2, "选题的因果逻辑：为什么这个问题成立", "ROOT CAUSE → CONSEQUENCE → WHY EXISTING TOOLS FAIL")
    chains = [
        (
            "来源割裂",
            "工程问答社区与学术论文各自成体系，字段、检索入口互不相通",
            "同一技术知识分散两端，用户要来回搜索才能拼出完整路径",
            FOREST,
        ),
        (
            "无知识状态感知",
            "传统检索/推荐只按关键词相关度或热度排序，不记录用户已掌握什么",
            "已掌握内容反复出现，真正的新知识、深化知识被淹没",
            ORANGE,
        ),
        (
            "结果不可追溯",
            "推荐或摘要环节不保留来源与计算依据，尤其当结论由 LLM 直接生成",
            "用户和答辩场景都无法验证推荐是否可信、证据在哪里",
            GOLD,
        ),
    ]
    for i, (title, cause, effect, color) in enumerate(chains):
        x = 1.25 + i * 3.72
        add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.55, 3.42, 2.85, fill=LIGHT_MINT, line=SAGE)
        add_pill(slide, f"0{i + 1}", x + 0.22, 1.72, 0.65, fill=color, color=WHITE, size=11)
        add_text(slide, title, x + 0.98, 1.72, 2.3, 0.35, size=17, color=color, bold=True)
        add_text(slide, "根因", x + 0.25, 2.2, 1.0, 0.24, size=11, color=color, bold=True)
        add_text(slide, cause, x + 0.25, 2.46, 2.9, 0.85, size=12.5, color=DARK, line_spacing=1.08)
        add_text(slide, "↓ 导致", x + 0.25, 3.3, 1.4, 0.22, size=11, color=MUTED, bold=True)
        add_text(slide, "后果", x + 0.25, 3.55, 1.0, 0.24, size=11, color=color, bold=True)
        add_text(slide, effect, x + 0.25, 3.79, 2.9, 0.58, size=12.5, color=DARK, line_spacing=1.08)
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 1.55, 4.62, 10.25, 1.85, fill=PAPER, line=FOREST)
    add_text(slide, "三个根因共同指向同一个缺口", 1.9, 4.8, 5.0, 0.32, size=15, color=FOREST, bold=True)
    add_text(
        slide,
        "现有搜索/推荐既不知道用户已有知识，也不保留来源与计算依据，因此无法做到“只发现值得学的新知识，且可验证”。",
        1.9,
        5.14,
        9.55,
        0.55,
        size=13.5,
        color=DARK,
        line_spacing=1.1,
    )
    add_text(
        slide,
        "本项目的应对：Stack Exchange + arXiv 提供多源真实文本 → Spark 完成可复现的清洗与特征计算 → MongoDB 保留全部原文与计算结果 → Web 呈现可解释、可追溯的个性化推荐。",
        1.9,
        5.68,
        9.55,
        0.68,
        size=13.5,
        color=FOREST,
        bold=True,
        line_spacing=1.1,
    )
    add_footer(slide, 2)
    add_notes(slide, "建议用时 55 秒。按“根因→后果”讲三张卡片，再讲清楚三个根因如何共同指向同一个缺口，最后一句话说明项目技术选择是在回应这个缺口，而不是堆砌技术名词。")


def sources(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 3, "数据来源：工程实践 + 学术研究", "VERIFIED CAPACITY, TRACEABLE LICENSES")
    add_card(
        slide,
        "主数据源｜Stack Exchange",
        [
            "站点：Stack Overflow、Server Fault、Super User、Ask Ubuntu",
            "已核验容量：仅 Stack Overflow 问题数已超过 2,400 万",
            "目标：采集 12,000～15,000 条，清洗后有效入库 ≥10,000",
            "获取：官方 Data Dump 优先，API 分页作为备用",
            "合规：逐条保存原始链接、署名和 content_license",
        ],
        1.28,
        1.55,
        5.25,
        4.85,
        accent=FOREST,
        fill=PAPER,
        body_size=14,
    )
    add_card(
        slide,
        "补充数据源｜arXiv CS",
        [
            "来源：arXiv Atom API 的标题、摘要、作者和分类",
            "已核验容量：cs.* 元数据约 93 万条",
            "用途：将工程问题关联到学术研究，体现来源异构",
            "默认不依赖 arXiv 满足一万条基线",
            "合规：元数据 CC0；论文全文许可逐篇区分",
        ],
        6.8,
        1.55,
        5.25,
        4.85,
        accent=ORANGE,
        fill=PAPER,
        body_size=14,
    )
    add_pill(slide, "B 站 / RSS：许可与稳定性确认后才进入特色范围，不承担核心规模", 2.48, 6.46, 8.4, fill=MINT, size=12)
    add_footer(slide, 3)
    add_notes(slide, "建议用时 50 秒。平台总量只证明来源容量；最终规模必须用项目实际去重后、成功写入 MongoDB 的唯一文档数证明。")


def architecture(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 4, "实施方案：先完成一条最小可运行闭环", "MINIMUM END-TO-END VERTICAL SLICE")
    steps = [
        ("多源数据", "Stack Exchange\narXiv", FOREST),
        ("Spark", "清洗 · 去重\nTF-IDF · 相似度", SAGE),
        ("MongoDB", "原文 · 结果\n用户知识记录", ORANGE),
        ("Flask API", "文档 · 主题\n画像 · 推荐", GOLD),
        ("Web", "主题选择\n新/深化知识", FOREST),
    ]
    for idx, (title, body, color) in enumerate(steps):
        x = 1.18 + idx * 2.28
        add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.05, 1.9, 2.65, fill=LIGHT_MINT, line=color)
        add_pill(slide, str(idx + 1), x + 0.58, 2.29, 0.7, fill=color, color=WHITE, size=12)
        add_text(slide, title, x + 0.12, 3.02, 1.66, 0.38, size=17, color=color, bold=True, align=PP_ALIGN.CENTER)
        add_text(slide, body, x + 0.15, 3.65, 1.6, 0.62, size=13, color=DARK, align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)
        if idx < len(steps) - 1:
            arrow = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 1.91), Inches(3.0), Inches(0.37), Inches(0.45))
            set_shape_fill(arrow, SAGE)
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 2.05, 5.25, 9.2, 0.86, fill=PAPER, line=SAGE)
    add_text(slide, "先用 100 + 100 条完成技术切片", 2.38, 5.51, 3.5, 0.3, size=15, color=FOREST, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "→", 6.0, 5.48, 0.5, 0.3, size=20, color=ORANGE, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "贯通后再扩展到有效文档 ≥10,000", 6.53, 5.51, 4.35, 0.3, size=15, color=FOREST, bold=True, align=PP_ALIGN.CENTER)
    add_footer(slide, 4)
    add_notes(slide, "建议用时 55 秒。沿箭头说明每层做什么、能留下什么证据。技术切片必须先贯通，再扩大规模。")


def mining(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 5, "核心挖掘与个性化机制", "RECOMMEND CONTENT THAT IS RELEVANT BUT NOT REPETITIVE")
    add_card(
        slide,
        "Spark 可解释挖掘",
        [
            "HTML 清洗、空文档过滤与去重",
            "CountVectorizer / HashingTF + IDF",
            "每篇文档 top-k 关键词与主题相关度",
            "文档相似度及非个性化基线",
            "使用 Precision@K / NDCG 或人工相关性评分评价",
        ],
        1.28,
        1.55,
        5.18,
        4.9,
        accent=FOREST,
        body_size=14,
    )
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 6.78, 1.55, 5.27, 4.9, fill=PAPER, line=SAGE)
    add_text(slide, "用户已有知识", 7.08, 1.82, 2.1, 0.36, size=18, color=ORANGE, bold=True)
    add_text(slide, "显式主题 · 已读文档 · 已确认知识卡 · 用户反馈", 7.08, 2.28, 4.55, 0.38, size=13, color=MUTED)
    statuses = [
        ("known｜已知", "高度覆盖且增量低 → 默认过滤", MUTED),
        ("refine｜深化", "已有主题相关 + 明显新增关键词", ORANGE),
        ("new｜新知识", "相邻主题 + 较高知识增量", FOREST),
    ]
    for idx, (name, body, color) in enumerate(statuses):
        y = 2.92 + idx * 0.91
        add_pill(slide, name, 7.08, y, 1.62, fill=color, color=WHITE, size=11)
        add_text(slide, body, 8.9, y + 0.05, 2.78, 0.31, size=13, color=DARK)
    add_text(slide, "LLM 只辅助解释，不承担核心分数、规模证明或 Spark 运行证据。", 7.08, 5.86, 4.55, 0.32, size=12, color=RED, bold=True)
    add_footer(slide, 5)
    add_notes(slide, "建议用时 60 秒。举例：用户已知 Python、Spark，系统过滤基础语法，保留 partition、streaming 等新增主题。可能冲突只作为后续提升项。")


def runtime(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 6, "运行策略：真实集群优先，local 明确降级", "PRIMARY AND FALLBACK EXECUTION PATHS")
    add_card(
        slide,
        "主路径｜真实分布式 Spark",
        [
            "优先申请课程 standalone / YARN 集群",
            "保存 master、application ID、worker/executor、DAG 和 event log",
            "用多节点任务分布证明真实分布式运行",
            "更符合课程底层架构评分方向",
        ],
        1.28,
        1.62,
        5.18,
        4.5,
        accent=FOREST,
        body_size=14,
    )
    add_card(
        slide,
        "备用路径｜Spark local[*]",
        [
            "仅在集群不可用且教师明确允许时采用",
            "仍运行真实 DataFrame / SQL / MLlib",
            "保持相同数据契约和结果格式",
            "明确标注单机模式，不冒充分布式集群",
        ],
        6.8,
        1.62,
        5.18,
        4.5,
        accent=ORANGE,
        body_size=14,
    )
    add_pill(slide, "第 2 天环境闸门：Spark 集群、MongoDB 实例、权限与版本必须得到结论", 2.45, 6.3, 8.55, fill=MINT, size=12)
    add_footer(slide, 6, "待确认：当前本机尚无可用 Spark / MongoDB")
    add_notes(slide, "建议用时 50 秒。课程只说明真实集群优于单机，没有具体扣分数。MongoDB 不可用时不能用 SQLite 冒充。")


def scope(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 7, "范围控制：基线必须稳，亮点允许放弃", "BASELINE FIRST, OPTIONAL FEATURES LATER")
    rows = [
        ("基线｜必须完成", "≥10,000 文档 · Spark 挖掘 · MongoDB · API · Web · 个性化 · 全套证据", FOREST),
        ("提升｜基线稳定后", "跨来源关联 · 新/已知/深化解释 · 关键词或推荐质量评价", ORANGE),
        ("惊喜｜不影响基线", "B 站/RSS 增量 · 反馈校准 · 工程问答到论文扩展 · 丰富可视化", GOLD),
    ]
    for idx, (title, body, color) in enumerate(rows):
        y = 1.6 + idx * 1.55
        add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 1.3, y, 10.72, 1.18, fill=PAPER, line=color)
        add_pill(slide, title, 1.58, y + 0.36, 2.25, fill=color, color=WHITE, size=12)
        add_text(slide, body, 4.12, y + 0.32, 7.45, 0.5, size=15, color=DARK, valign=MSO_ANCHOR.MIDDLE)
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 3.1, 6.13, 7.15, 0.58, fill=LIGHT_MINT, line=SAGE)
    add_text(slide, "第 10 天基线未稳定 → 停止所有惊喜功能，只修复主链路", 3.35, 6.31, 6.65, 0.25, size=13, color=RED, bold=True, align=PP_ALIGN.CENTER)
    add_footer(slide, 7)
    add_notes(slide, "建议用时 40 秒。强调不会为了复杂而增加微服务、复杂权限或知识图谱，惊喜功能不是完成条件。")


def schedule(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 8, "三周实施计划：先消除高风险，再扩展规模", "THREE-WEEK EXECUTION PLAN WITH EVIDENCE")
    weeks = [
        (
            "第 1 周｜确认",
            ["陶文杰：Spark 环境、Stack Exchange 样本", "陈铭宇：MongoDB、arXiv、Spec Kit", "证据：环境记录、字段字典、许可与规模估算"],
            FOREST,
        ),
        (
            "第 2 周｜贯通",
            ["陶文杰：Spark 清洗、TF-IDF、推荐算法", "陈铭宇：MongoDB、API、Web 与用户画像", "证据：100+100 切片 → 有效文档 ≥10,000"],
            ORANGE,
        ),
        (
            "第 3 周｜证明",
            ["共同：评价、联调、稳定性与完整复跑", "规约、报告、PPT、Git、≤5 分钟视频", "证据：指标、日志、查询、API、页面截图"],
            GOLD,
        ),
    ]
    for idx, (title, items, color) in enumerate(weeks):
        add_card(slide, title, items, 1.25 + idx * 3.72, 1.58, 3.42, 4.55, accent=color, fill=PAPER, body_size=13)
    add_pill(slide, "D2 环境闸门", 1.8, 6.32, 2.45, fill=FOREST, color=WHITE, size=11)
    add_pill(slide, "D5 选题闸门", 5.43, 6.32, 2.45, fill=ORANGE, color=WHITE, size=11)
    add_pill(slide, "D10 基线闸门", 9.05, 6.32, 2.45, fill=GOLD, color=WHITE, size=11)
    add_footer(slide, 8)
    add_notes(slide, "建议用时 60 秒。补充说明：每个阶段都写明负责人、依赖、完成证据和失败降级；第 5 天选题通过后才进入正式开发。")


def division(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 9, "初版分工：两人各 50%，都参与核心模块", "BALANCED OWNERSHIP AND CROSS-REVIEW")
    add_card(
        slide,
        "陶文杰｜50%",
        [
            "数据源与 Spark 层：采集、清洗、去重和万条任务",
            "挖掘与评价：TF-IDF、相似度、排序和指标",
            "核心证据：数据统计、Spark DAG、日志、性能结果",
            "答辩备份：能独立启动并解释 API / Web",
        ],
        1.28,
        1.6,
        5.18,
        4.78,
        accent=FOREST,
        body_size=14,
    )
    add_card(
        slide,
        "陈铭宇｜50%",
        [
            "MongoDB 与业务层：集合、索引、幂等写入和 API",
            "用户与交互层：知识画像、推荐页面和反馈",
            "核心证据：Mongo 查询、API JSON、页面与规约",
            "答辩备份：能解释 Spark master、TF-IDF 和规模证据",
        ],
        6.8,
        1.6,
        5.18,
        4.78,
        accent=ORANGE,
        body_size=14,
    )
    add_pill(slide, "共同评审：数据契约 · 推荐公式 · 四层架构 · 最终演示", 3.15, 6.48, 7.05, fill=MINT, size=12)
    add_footer(slide, 9)
    add_notes(slide, "建议用时 40 秒。贡献比例是初版，最终按真实 Git 提交和交付调整，并说明与初版差异。")


def risks(prs, blob):
    slide = add_slide(prs, blob)
    add_header(slide, 10, "风险闸门与最终验收证据", "FAIL EARLY, KEEP THE BASELINE DEMONSTRABLE")
    add_card(
        slide,
        "高风险事项 → 失败替代",
        [
            "无真实集群 → 经教师批准使用 Spark local[*]",
            "MongoDB 不可用 → 申请课程/远程实例，不用 SQLite 冒充",
            "API 配额不足 → Data Dump、API Key 或教师数据集",
            "B 站/RSS 许可不清 → 直接取消，不影响基线",
            "冲突知识误报高 → 只保留新/已知/深化",
        ],
        1.28,
        1.58,
        5.18,
        4.95,
        accent=RED,
        body_size=13,
    )
    add_card(
        slide,
        "最终证据链",
        [
            "数据：来源、许可、实采、去重、有效和入库数量",
            "Spark：master、executor、DAG、输入输出和耗时",
            "MongoDB：集合、索引、查询和批次版本",
            "系统：API JSON、浏览器页面和演示视频",
            "过程：Spec Kit、提示词、Git 历史和分工表",
        ],
        6.8,
        1.58,
        5.18,
        4.95,
        accent=FOREST,
        body_size=13,
    )
    add_footer(slide, 10)
    add_notes(slide, "建议用时 50 秒。风险不逐条展开，只讲四个会导致项目不达标的点，以及如何保住基线。")


def close(prs, blob):
    slide = add_slide(prs, blob)
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, 1.45, 1.1, 10.45, 5.45, fill=PAPER, line=SAGE)
    add_pill(slide, "期待形成的课程成果", 4.53, 1.52, 4.25, fill=MINT, size=14)
    add_text(slide, "一个可运行、可答辩、可追溯的", 2.15, 2.25, 9.05, 0.52, size=28, color=FOREST, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "个性化技术知识发现系统", 2.15, 2.86, 9.05, 0.52, size=28, color=DARK, bold=True, align=PP_ALIGN.CENTER)
    results = ["多源真实文档", "Spark 可解释挖掘", "MongoDB 文档管理", "Web 个性化发现", "完整课程证据"]
    for idx, item in enumerate(results):
        add_pill(slide, item, 1.87 + idx * 1.94, 3.82, 1.72, fill=FOREST if idx % 2 == 0 else ORANGE, color=WHITE, size=11)
    add_text(slide, "选题确认阶段仍需教师确认：数据组合 · Spark 集群/local · MongoDB 实例 · 个性化评价口径", 2.05, 4.72, 9.2, 0.5, size=14, color=RED, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, "先证明链路，再追求亮点。", 3.78, 5.58, 5.8, 0.42, size=19, color=ORANGE, bold=True, align=PP_ALIGN.CENTER)
    add_notes(slide, "建议用时 30 秒。总结预期成果并提出需要教师确认的四项条件。全程预计约 8 分 40 秒。")


def build(source_path: Path, output_path: Path):
    source = Presentation(source_path)
    blob = background_blob(source)

    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    prs.core_properties.title = "基于 Spark 与 MongoDB 的多源技术内容知识挖掘与个性化发现 Web 系统"
    prs.core_properties.subject = "选题调研、实施方案与三周计划"
    prs.core_properties.author = "陶文杰, 陈铭宇"
    prs.core_properties.comments = f"Revised from {source_path.name}; original preserved"

    cover(prs, blob)
    overview(prs, blob)
    problem(prs, blob)
    sources(prs, blob)
    architecture(prs, blob)
    mining(prs, blob)
    runtime(prs, blob)
    scope(prs, blob)
    schedule(prs, blob)
    division(prs, blob)
    risks(prs, blob)
    close(prs, blob)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(output_path)
    print(f"Generated {output_path} with {len(prs.slides)} slides; source preserved: {source_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("Spark-MongoDB多源知识挖掘系统.pptx"))
    parser.add_argument("--output", type=Path, default=Path("Spark-MongoDB多源知识挖掘系统-实施方案补充版.pptx"))
    args = parser.parse_args()
    build(args.source, args.output)


if __name__ == "__main__":
    main()
