# -*- coding: utf-8 -*-
"""生成答辩 PPT，内容与 答辩讲稿.md 的 17 页一一对应。"""
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

TITLE_COLOR = RGBColor(0x1F, 0x3A, 0x5F)
ACCENT_COLOR = RGBColor(0x2E, 0x74, 0xB5)
TEXT_COLOR = RGBColor(0x33, 0x33, 0x33)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def add_title(slide, text, size=32):
    box = slide.shapes.add_textbox(Inches(0.6), Inches(0.35), Inches(12.1), Inches(1.0))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(size)
    p.font.bold = True
    p.font.color.rgb = TITLE_COLOR
    return box


def add_bullets(slide, items, top=1.5, left=0.7, width=11.9, height=5.5, size=20,
                 bullet_char="•"):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for item in items:
        if isinstance(item, tuple):
            text, level = item
        else:
            text, level = item, 0
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        prefix = ("    " * level) + (bullet_char + " " if bullet_char else "")
        p.text = prefix + text
        p.font.size = Pt(size - level * 2)
        p.font.color.rgb = TEXT_COLOR
        p.space_after = Pt(8)
    return box


def add_table(slide, rows, top=1.6, left=0.6, width=12.1, height=None, col_widths=None,
              font_size=15):
    nrows = len(rows)
    ncols = len(rows[0])
    if height is None:
        height = 0.5 * nrows
    gframe = slide.shapes.add_table(nrows, ncols, Inches(left), Inches(top),
                                     Inches(width), Inches(height))
    table = gframe.table
    if col_widths:
        for i, w in enumerate(col_widths):
            table.columns[i].width = Inches(w)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = str(val)
            for p in cell.text_frame.paragraphs:
                p.font.size = Pt(font_size)
                if r == 0:
                    p.font.bold = True
                    p.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                else:
                    p.font.color.rgb = TEXT_COLOR
            if r == 0:
                cell.fill.solid()
                cell.fill.fore_color.rgb = ACCENT_COLOR
    return gframe


def new_slide():
    return prs.slides.add_slide(BLANK)


# 第1页 封面
s = new_slide()
box = s.shapes.add_textbox(Inches(1.0), Inches(2.4), Inches(11.3), Inches(2.0))
tf = box.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "基于 Spark 与 MongoDB 的多源技术内容\n知识挖掘与个性化发现系统"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = TITLE_COLOR
p.alignment = PP_ALIGN.CENTER
box2 = s.shapes.add_textbox(Inches(1.0), Inches(4.6), Inches(11.3), Inches(1.2))
tf2 = box2.text_frame
p2 = tf2.paragraphs[0]
p2.text = "组员：陶文杰　陈铭宇"
p2.font.size = Pt(22)
p2.font.color.rgb = TEXT_COLOR
p2.alignment = PP_ALIGN.CENTER
p3 = tf2.add_paragraph()
p3.text = "大数据综合实践课程项目答辩"
p3.font.size = Pt(18)
p3.font.color.rgb = TEXT_COLOR
p3.alignment = PP_ALIGN.CENTER

# 第2页 项目目标
s = new_slide()
add_title(s, "项目目标")
add_bullets(s, [
    "从 Stack Exchange（技术问答，主源）与 arXiv CS（论文摘要，补充源）采集技术内容",
    "用 Spark 完成清洗、去重、关键词与主题挖掘、相似度计算",
    "面向单个用户做知识“已知/需深化/全新/疑似冲突”四态判定，并给出个性化推荐",
])

# 第3页 四层架构总览
s = new_slide()
add_title(s, "四层架构总览")
add_table(s, [
    ["层次", "课程定义", "本项目落地"],
    ["交互层", "用户交互、可视化输出", "Flask 页面 + 原生 JS fetch"],
    ["业务逻辑层", "承接前端请求、调度数据与挖掘接口", "Flask Blueprint 路由、鉴权、判定规则"],
    ["数据管理与挖掘层", "存储/检索/管理/分析挖掘", "MongoDB 集合设计与索引、聚合查询"],
    ["大数据底层处理层", "大规模分布式存储与计算", "PySpark：TF-IDF/KMeans/相似度/推荐分数"],
], top=1.6, height=3.6, col_widths=[3.0, 4.5, 4.6])

# 第4页 技术选型与取舍
s = new_slide()
add_title(s, "技术选型与取舍")
add_bullets(s, [
    "批处理引擎：Spark（不用 Flink/Kafka）—— 一次性批量挖掘任务，无需流式处理",
    "存储：MongoDB（不用图数据库/键值库/时序库）—— 半结构化文档契合技术内容",
    "宪法明确禁止：“线程池代替 Spark”“JSONL/SQLite 代替 MongoDB”“CLI/Markdown 代替 Web”",
    "鉴权：Flask 内置 session + Werkzeug 密码哈希（不引入 Flask-Login/JWT，避免过度设计）",
])

# 第5页 SDD 方法论
s = new_slide()
add_title(s, "SDD 方法论")
add_bullets(s, [
    "speckit-specify → spec.md（用户故事/验收标准/边界情况，不含实现细节）",
    "speckit-plan → research.md（技术决策+理由+备选方案）",
    ("plan.md（宪法逐条自查）+ data-model.md + contracts/ + quickstart.md", 1),
    "speckit-tasks → tasks.md（按用户故事分组的任务清单，标注依赖与并行关系）",
    "speckit-implement → 代码 + tasks.md 勾选留痕 + evidence/acceptance-record.md",
])

# 第6页 项目宪法核心原则
s = new_slide()
add_title(s, "项目宪法核心原则")
add_bullets(s, [
    "Principle I（不可协商）：TF-IDF、KMeans、相似度、推荐分数必须由 Spark 产出并留有可检查证据",
    ("LLM 只能做推荐理由润色、“疑似冲突”候选生成等辅助环节，不能替代核心计算结果", 1),
    "Principle II：分类判定对象是“知识单元”（关键词/主题簇），不是整篇文档",
    ("每次判定必须可追溯到具体依据；possible_conflict 须人工复核后才对用户可见", 1),
])

# 第7页 Feature 1 概览
s = new_slide()
add_title(s, "Feature 1 —— 数据管道与 Spark 挖掘（陶文杰负责）")
add_bullets(s, [
    "目标：Stack Exchange + arXiv CS 采集 → Spark 清洗/去重/TF-IDF/KMeans/相似度 → MongoDB",
    "数据来源：Stack Exchange 四站点轮换（stackoverflow/serverfault/superuser/askubuntu）+ arXiv",
    "Spark 计算：PySpark DataFrame + MLlib —— TF-IDF 关键词、KMeans 主题聚类、文档相似度",
    "3 个用户故事：US1 最小链路（100+100）/ US2 规模化（≥10,000）/ US3 幂等更新",
])

# 第8页 Feature 1 数据模型
s = new_slide()
add_title(s, "Feature 1 —— MongoDB 数据模型")
add_bullets(s, [
    "documents：doc_id / source / title / body_text / quality / batch_id；唯一索引 source+doc_id",
    "mining_results：doc_id / batch_id / keywords[] / topic_cluster_id / similar_doc_ids / reliable",
    ("同一文档跨批次的历史结果保留、不覆盖，documents 只维护指向最新批次的摘要引用", 1),
    "batches：batch_id / started_at / finished_at / input_count / valid_count / failed_count",
], size=19)

# 第9页 Feature 1 问题与修复
s = new_slide()
add_title(s, "Feature 1 —— 遇到的问题与修复", size=28)
add_bullets(s, [
    "① 性能缺陷：相似度两两比较是 O(簇大小²) 的纯 Python 循环，KMeans 分簇不均衡，10500 条时进程被杀死",
    ("修复：超阈值簇先用小规模 Spark KMeans 递归拆分成有界子组再比较；3000条103s，10500条240s均可跑完", 1),
    "② 数据完整性缺陷：Stack Exchange 跨站点 question_id 撞号，source 字段为常量导致 11 条静默覆盖",
    ("修复：doc_id 增加站点前缀（如 serverfault-12345），保证全局唯一", 1),
], size=18)

# 第10页 Feature 1 验收结果
s = new_slide()
add_title(s, "Feature 1 —— 验收结果")
add_bullets(s, [
    "环境验证：PySpark 4.2.0 + Java 17 + MongoDB 7.0.14（真实实例）全部通过",
    "US1：100+100 条最小链路（batch_id 20260905-7442679d），运行统计查询耗时 0.007 秒",
    "US3：3 个幂等更新测试用例全部通过",
    "US2：≥10,000 条规模化因 API 配额+限流暂标“待补充”，已验证性跑通 9,986 条确认修复生效",
    "测试套件：35 个单元测试全部通过",
], size=19)

# 第11页 Feature 2 概览
s = new_slide()
add_title(s, "Feature 2 —— 个性化知识分类与 Web 展示（陈铭宇负责）")
add_bullets(s, [
    "目标：基于 Feature 1 产出，判定每个知识单元 known/refine/new/possible_conflict 四态",
    "提供鉴权 API 与 Web 页面展示个性化推荐",
    "3 个用户故事：US1 最小垂直切片 / US2 规模化判定+基线对比 / US3 反馈闭环",
])

# 第12页 核心架构取舍
s = new_slide()
add_title(s, "核心架构取舍：分类判定为什么不用 Spark", size=28)
add_bullets(s, [
    "分类判定（known/refine/new/possible_conflict）：纯 Python 规则函数，不经 Spark",
    "推荐分数：PySpark DataFrame 批量计算",
    "score = α×主题相关度 + β×新增关键词比例 + γ×文档质量 - ε×已读惩罚",
    "理由：分类判定依赖单个用户画像，画像变化频率远高于文档规模变化；",
    ("强制用 Spark 会导致每次修改画像都要重跑作业，与“实时反映”要求冲突", 1),
], size=19)

# 第13页 数据模型与状态机
s = new_slide()
add_title(s, "Feature 2 —— 数据模型与状态机")
add_bullets(s, [
    "users / user_profiles（known_topics/known_keywords/read_doc_ids/feedback_history）",
    "user_knowledge：user_id + knowledge_id 复合唯一索引，格式 kw:{term} 或 topic:{id}",
    "状态机：",
    ("关键词/主题簇均未命中 → new", 1),
    ("命中主题簇且有明显新增内容 → refine", 1),
    ("关键词高度重合 → known", 1),
    ("疑似冲突 + 人工复核通过 → possible_conflict（复核前对外仍显示 new/refine）", 1),
], size=18)

# 第14页 API 契约与鉴权模型
s = new_slide()
add_title(s, "API 契约与鉴权模型")
add_bullets(s, [
    "8 个接口（公开 4 个 / 需登录 4 个）",
    "公开：register / login / topics / documents/{source}/{doc_id}",
    "需登录：logout / recommendations（mode=personalized|baseline） / profile/topics / profile/feedback",
    "鉴权模型：Flask session；未登录 → 401；session 有效但请求他人 user_id → 403（直接拒绝不静默改写）",
], size=19)

# 第15页 Feature 2 真实验收
s = new_slide()
add_title(s, "Feature 2 —— 真实验收（5 个 quickstart 步骤）", size=28)
add_bullets(s, [
    "① 最小链路：注册→登录→设置已知主题→推荐结果带判定依据 ✅",
    "② 新用户默认全部 new（无画像基础不臆断已知） ✅",
    "③ 鉴权边界：未登录 401、越权请求 403 ✅",
    "④ 反馈闭环：提交“确认已知”后，对应知识单元由 new 变为 known ✅",
    "⑤ 规模化+基线对比：personalized 与 baseline 两组结果明显不同 ✅",
    "全部基于真实 MongoDB + 真实 local[*] Spark，未使用测试替身",
], size=18)

# 第16页 两个Feature集成边界
s = new_slide()
add_title(s, "两个 Feature 的集成边界")
add_bullets(s, [
    "Feature 2 只读依赖 Feature 1 的 documents/mining_results 集合",
    "不修改其结构，不导入其内部实现，仅通过 MongoDB 查询",
    "唯一显式复用：knowpipe/mining/batch.py 的批次证据结构（BatchStats/new_batch_id）",
    "代码审计（T050）确认：knowpipe/web/*.py 中未出现任何写操作，只读边界经过硬性检查",
])

# 第17页 分工、测试与总结
s = new_slide()
add_title(s, "分工、测试与总结")
add_table(s, [
    ["成员", "负责层次/模块", "具体工作", "占比"],
    ["陶文杰", "数据源与 Spark 层、挖掘与评价层",
     "采集/清洗去重/TF-IDF；相似度计算、基线对比与指标评测", "50%"],
    ["陈铭宇", "MongoDB 与业务层、用户与交互层",
     "集合设计/索引/幂等写入、业务 API；用户画像、推荐页面开发", "50%"],
], top=1.5, height=1.8, col_widths=[1.4, 3.6, 5.8, 1.3], font_size=14)
add_bullets(s, [
    "测试总览：全仓库 88 个单元/契约测试全部通过（Feature 1: 35个，Feature 2 新增 53个）",
    "含 mongomock 单测 + 真实 MongoDB/local[*] Spark 的规模验证",
    "遗留项：Feature 1 万级规模最终验收待外部 API 限流解除后补充",
], top=3.7, size=18)

prs.save("/home/lengjing1236/knowpipe/defense/答辩PPT.pptx")
print("saved")
