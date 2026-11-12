"""运行配置：全部来自环境变量（由部署平台注入：Modal 的 Secret、Hugging Face Space 的 Variables and secrets 等）。"""
import json
import hashlib
import hmac
import os
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


BASE_DIR = Path(__file__).resolve().parent.parent

# ---------- 模型 ----------
# 打分用的两个语言模型：必须共用同一个分词器（同一系列的基础版 + 对话版）。
OBSERVER_MODEL = os.getenv("OBSERVER_MODEL", "Qwen/Qwen2.5-0.5B")
PERFORMER_MODEL = os.getenv("PERFORMER_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")
# 中文 AI 文本分类器（MPU，ICLR 2024）。留空则不启用。
# 自主训练的中文 / 英文主分类器（2026-11-05 起试验，替换外部的 zhv3 / desklib）：填本仓库 Release 名（如 chinese-classifier-own1），
# 部署 / 验收工作流把它解压到 server/chinese-main-classifier、server/english-main-classifier。留空 = 仍用外部模型。
ZH1_CLASSIFIER_ID = os.getenv("ZH1_CLASSIFIER_ID", "")
EN1_CLASSIFIER_ID = os.getenv("EN1_CLASSIFIER_ID", "")
_ZH1_DIR, _EN1_DIR = BASE_DIR / "chinese-main-classifier", BASE_DIR / "english-main-classifier"
_ZH1_ON = bool(ZH1_CLASSIFIER_ID) and _ZH1_DIR.exists()
_EN1_ON = bool(EN1_CLASSIFIER_ID) and _EN1_DIR.exists()
CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", str(_ZH1_DIR) if _ZH1_ON else "yuchuantian/AIGC_detector_zhv3")
# 分类器中哪个标签表示"AI 生成"。auto = 按标签名自动判断，判断不了时取下标 1。
CLASSIFIER_AI_LABEL = os.getenv("CLASSIFIER_AI_LABEL", "auto")
# 英文 AI 文本分类器（desklib，DeBERTa-v3-large，RAID 基准）。留空则英文只用语言模型特征。
EN_CLASSIFIER_MODEL = os.getenv("EN_CLASSIFIER_MODEL", str(_EN1_DIR) if _EN1_ON else "desklib/ai-text-detector-v1.01")
# 校准里记录的模型名：自主模型记 Release 名（各机器上的目录路径不同），外部模型记 Hugging Face 名
CLASSIFIER_NAME = ZH1_CLASSIFIER_ID if (ZH1_CLASSIFIER_ID and Path(CLASSIFIER_MODEL).is_dir()) else CLASSIFIER_MODEL
EN_CLASSIFIER_NAME = EN1_CLASSIFIER_ID if (EN1_CLASSIFIER_ID and Path(EN_CLASSIFIER_MODEL).is_dir()) else EN_CLASSIFIER_MODEL
ENABLE_EN_CLASSIFIER = _bool("ENABLE_EN_CLASSIFIER", True)
EN_CLS_MAX_TOKENS = _int("EN_CLS_MAX_TOKENS", 512)
# 英文第二分类器（tools/train_english.py：arXiv / MAGE 人写 vs DeepSeek、文心一言等国产大模型写的英文论文段落，
# 发布在本仓库 Release english-classifier-v1）。与 desklib（主要见过 GPT、LLaMA 等国外模型）互补。填本地目录；留空则不用。
EN2_CLASSIFIER_MODEL = os.getenv("EN2_CLASSIFIER_MODEL", "")
# v14（2026-10-22）：v3 只见过论文，把当代出版小说（《At First Sight》等）普遍打到 0.9 以上，逐段判断因此误判真人小说。
# v14 加入当代小说（BookCorpusOpen + 用户提供的电子书，按作者划分）与同题材的 AI 小说后，接入逐段校准（见 MODEL_UPDATES.md）。
EN2_CLASSIFIER_ID = os.getenv("EN2_CLASSIFIER_ID", "english-classifier-v29")
# 中文第二分类器（tools/train_chinese.py 训练）：专门识别新一代国产大模型（DeepSeek / Kimi / 文心 / 千问 / 豆包）的中文，
# 尤其是散文、游记、回忆类文学文字。只用于"整篇判断"（不参与逐段校准）。
ZH2_CLASSIFIER_MODEL = os.getenv("ZH2_CLASSIFIER_MODEL", "")
# 英文整篇分类器（另一版英文第二分类器，加入了故事、散文、清单等非论文体裁）：只用于非论文英文作品的"整篇判断"，
# 不参与逐段校准（v5 / v6 换进逐段校准后真人误判升高，已否决）。
EN3_CLASSIFIER_MODEL = os.getenv("EN3_CLASSIFIER_MODEL", "")
EN3_CLASSIFIER_ID = os.getenv("EN3_CLASSIFIER_ID", "english-classifier-v7")
# 依据（english-classifier-v7，2026-10-04）：397 篇真人英文长文（CNN 新闻 150、Reddit 写作社区故事 150、IMDB 长影评 97）
# 整篇中位数最高 0.855、99 分位 0.82，没有一篇 ≥ 0.9；文心清单 0.95、文心童话 0.93、千问 / 豆包故事 0.89–0.95。
EN3_DOC_THRESHOLD = float(os.getenv("EN3_DOC_THRESHOLD", "0.92") or 0.92)
# 第二个英文整篇分类器（english-classifier-v9：训练时加入 1765 篇非论文真人英文——新闻、故事、影评、学生论文、古腾堡经典散文）。
# 单独用于整篇判断不够可靠（真人 CNN 新闻最高也到 0.956），但与 v7 联合时很干净：
#   "v7 整篇 ≥ EN3_JOINT_THRESHOLD 且 v9 整篇 ≥ EN4_JOINT_THRESHOLD" → 394 篇真人英文长文 0 篇命中，用户的 17 篇 AI 文档全部命中
#   （包括豆包田园散文《Of Fields and Seasons》：v7 0.857 / v9 0.956；最接近的真人：v7 0.851 / v9 0.947）。
EN4_CLASSIFIER_MODEL = os.getenv("EN4_CLASSIFIER_MODEL", "")
EN4_CLASSIFIER_ID = os.getenv("EN4_CLASSIFIER_ID", "english-classifier-v29")
# v14（2026-10-22）：v11 把真人当代小说大量打到 0.9 以上（《At First Sight》146 段中 112 段、用户另给的 40 本书 320 段中 68 段）；
#   v14 在 45 本没参与训练的书 540 段中 9 段、《At First Sight》中 24 段。
#   397 篇真人英文长文：v14 整篇最高约 0.90；"v7 ≥ 0.75 且 v14 ≥ 0.93" → 真人 0/397、用户 AI 19/21（v11 20/21）。
# v11（2026-10-20）：在 v10 的基础上加入用户提供的 124 篇 AI 英文（故事、书信、论文）。真人与 AI 分得更开：
#   397 篇真人英文长文里 v11 整篇最高 0.93（v10 为 0.956），用户 21 篇 AI 文档几乎都 ≥ 0.95；
#   "v7 ≥ 0.75 且 v11 ≥ 0.93"→ 真人 0/397、用户 AI 20/21（v10 最好的一档是 0/397、19/21）。
#   1015.docx 的真人小说片段（Sue Grafton）v10 0.956 → v11 0.931，离误判更远。
# v10（2026-10-04）：在 v9 基础上加入 253 篇"去 AI 味"改写的英文（DeepSeek / 千问 / 豆包 / Kimi / 文心先写、再按"降 AI 率"
# 指令改写），专门针对用户用 ChatGPT / Gemini 写的"规避检测"文章。验证（397 篇真人英文长文 + 21 篇用户 AI 文档）：
#   v7 ≥ 0.85 且 v10 ≥ 0.95 → 真人 0 篇，用户 AI 19 篇；另一档 v7 ≥ 0.75 且 v10 ≥ 0.955 → 真人 0 篇，再多认出 The Seam in the Night
EN3_JOINT_THRESHOLD = float(os.getenv("EN3_JOINT_THRESHOLD", "0.85") or 0.85)
EN4_JOINT_THRESHOLD = float(os.getenv("EN4_JOINT_THRESHOLD", "0.93") or 0.93)
EN4_SOLO_THRESHOLD = float(os.getenv("EN4_SOLO_THRESHOLD", "0.93") or 0.93)
EN4_SOLO_MIN_SEGS = int(os.getenv("EN4_SOLO_MIN_SEGS", "6") or 6)
EN3_JOINT2_THRESHOLD = float(os.getenv("EN3_JOINT2_THRESHOLD", "0.75") or 0.75)
EN4_JOINT2_THRESHOLD = float(os.getenv("EN4_JOINT2_THRESHOLD", "0.94") or 0.94)
# 整篇判断只带上自己的整篇分类器得分 ≥ 这个值的段落（插在 AI 文章中间的真人段落不跟着计入）
DOC_CARRY_FLOOR = float(os.getenv("DOC_CARRY_FLOOR", "0.5") or 0.5)
# 整篇像 AI 时，本段的整篇分类器（v7）自己也 ≥ 这个值，也跟着计入（2026-10-27）：EN4 与逐段的第二分类器是同一个模型（v14），
# 对公式多、术语密的段落（Gemini《后量子密码》《火山碎屑流》等）只有 0.1–0.3，原来只看它就被挡在整篇判断之外；
# 这些段落 v7 都 ≥ 0.9。用户 59 篇纯 AI 文档中有 15 段属于这种情况，16 本真人自我提升书中 0 段。
EN3_OWN_CARRY = float(os.getenv("EN3_OWN_CARRY", "0.9") or 0.9)
# 千问套语整篇判断（engine._qwen_style）：本篇未过阈值的段落，英文分类器自身得分至少达到这个值才计入
QW_STYLE_OWN = float(os.getenv("QW_STYLE_OWN", "0.5") or 0.5)
# 千问短语表（engine._qwen_lex_hits）：任意 4000 词窗口命中至少这么多条（互相包含只算一条）时按千问套语处理
# 千问第三版短语表（engine._qwen_lex3）：4000 词窗口命中条数下限；命中时分类器自身得分 ≥ QW_LEX3_OWN 的段落计入
QW_LEX3_MIN = int(os.getenv("QW_LEX3_MIN", "6") or 6)
QW_LEX3_OWN = float(os.getenv("QW_LEX3_OWN", "0.0") or 0.0)
QW_LEX_MIN = int(os.getenv("QW_LEX_MIN", "4") or 4)
# DeepSeek 网页版固定句式：任意 4000 词窗口命中 deepseek_lex.json 里 ≥ 4 条（真人文本最高 2 条）
DS_LEX_MIN = int(os.getenv("DS_LEX_MIN", "4") or 4)
DS_THING_MIN = int(os.getenv("DS_THING_MIN", "8") or 8)  # 1000 词内 "is the thing that" 次数；真人书最高 4，BookCorpusOpen 23509 个窗口最高 5
# 国产大模型中文分类器整篇中位数低于这个值（明确像人写）时，不用 MPU 中文分类器的整篇判断
ZH2_HUMAN_VETO = float(os.getenv("ZH2_HUMAN_VETO", "0.3") or 0.3)
# v7（2026-11-08）：加入 8 家平台 11-08 上传的现代文 AI 样本 265 段，并加 280 段真人现代散文 / 译文对照
#（v6 只加 AI 样本，把朱自清《背影》判到 0.93；v7 真人文学整篇最高 0.06，《背影》0.05）。
# v3（2026-10-04）：加入 287 篇"去 AI 味"改写的中文（五家国产模型先写再按"降 AI 率"指令改写，或一步要求"写得不像 AI"）。
# 没参与训练的 ChatGPT / Gemini"规避检测"中文文章 6 篇里 5 篇整篇 ≥ 0.92；没参与训练的真人文档整篇最高 0.05（v2 为 0.08）。
# 2026-11-11 查明：11-05 起各流程按“文件里第一个 chinese-classifier- 名字”取模型，实际取到的一直是 own1（v7 从未生效），
# 所有回归数字也都基于 own1。现改为严格按本项取 Release，并如实写 own1，行为不变。v8 试过：真人误判升高，未采用。
ZH2_CLASSIFIER_ID = os.getenv("ZH2_CLASSIFIER_ID", "chinese-classifier-own1")
# 诗词专用分类器（tools/train_poetry.py 在 ChangAn 上微调，发布在本仓库 Release）。填本地目录；留空则诗词用通用中文分类器。
POETRY_CLASSIFIER_MODEL = os.getenv("POETRY_CLASSIFIER_MODEL", "")
def _calibrated_classifier(profile: str, prefix: str, default: str) -> str:
    """诗词 / 文言分类器用哪一版，以内置校准里记录的为准：评估工作流用候选版本重新拟合校准并提交后，部署和检测都自动换成它，
    模型与校准参数永远一致（以前要同时手动改几处版本号，漏改一处就会用错校准）。"""
    try:
        c = json.loads((Path(__file__).resolve().parent / "default_calibration.json").read_text("utf-8"))
        v = (((c.get("profiles") or {}).get(profile) or {}).get("models") or {}).get("classifier") or ""
        return v if v.startswith(prefix) else default
    except Exception:  # noqa: BLE001
        return default


POETRY_CLASSIFIER_ID = os.getenv("POETRY_CLASSIFIER_ID") or _calibrated_classifier("zh_poetry", "poetry-classifier-", "poetry-classifier-v1")
POETRY_CLASSIFIER_URL = os.getenv(
    "POETRY_CLASSIFIER_URL",
    "https://github.com/zhuloujun/ceshi/releases/download/poetry-classifier-v1/poetry-classifier.tar.gz")

# 文言专用分类器（tools/train_classical.py 用古籍人写 vs DeepSeek / Kimi / 文心一言等生成的文言微调，发布在本仓库 Release）。
CLASSICAL_CLASSIFIER_MODEL = os.getenv("CLASSICAL_CLASSIFIER_MODEL", "")
CLASSICAL_CLASSIFIER_ID = (os.getenv("CLASSICAL_CLASSIFIER_ID")
                           or _calibrated_classifier("zh_classical", "classical-classifier-", "classical-classifier-v3"))

ENABLE_LM = _bool("ENABLE_LM", True)
ENABLE_CLASSIFIER = _bool("ENABLE_CLASSIFIER", True)

TORCH_THREADS = _int("TORCH_THREADS", os.cpu_count() or 2)
LM_MAX_TOKENS = _int("LM_MAX_TOKENS", 512)          # 单段送入语言模型的最大 token 数
LM_DTYPE = os.getenv("LM_DTYPE", "float32")          # float32（默认）/ bfloat16：大模型省一半内存
CLS_MAX_TOKENS = _int("CLS_MAX_TOKENS", 512)

# ---------- 分段 ----------
SEGMENT_TARGET_CHARS = _int("SEGMENT_TARGET_CHARS", 400)
SEGMENT_MIN_CHARS = _int("SEGMENT_MIN_CHARS", 80)
SEGMENT_TARGET_CHARS_EN = _int("SEGMENT_TARGET_CHARS_EN", 1000)   # 英文约 170 词
SEGMENT_MIN_CHARS_EN = _int("SEGMENT_MIN_CHARS_EN", 200)
# 遇到段落分隔时，窗口不足这么长就与下一段合并（同一作品、同一文体内）
SEGMENT_FLUSH_CHARS = _int("SEGMENT_FLUSH_CHARS", 200)
SEGMENT_FLUSH_CHARS_CLASSICAL = _int("SEGMENT_FLUSH_CHARS_CLASSICAL", 120)
SEGMENT_FLUSH_CHARS_EN = _int("SEGMENT_FLUSH_CHARS_EN", 1000)   # 约 170 词：英文分类器在 1000 字符以上的窗口明显更准（AUROC 0.93 → 0.95+）
# 现代汉语段落短于这个字数时，改用"短段"校准（有的话）：短文本信号弱，需要单独的阈值
SHORT_SEGMENT_CHARS = _int("SHORT_SEGMENT_CHARS", 200)
# 篇幅短的提示（Turnitin 要求英文至少 300 词才给结果；这里对单段放宽，只做标注）
SHORT_CHARS_ZH = _int("SHORT_CHARS_ZH", 100)
SHORT_WORDS_EN = _int("SHORT_WORDS_EN", 150)
# 一篇英文作品（或整篇文本）正文不足这么多词时不判定，只给参考值
EN_MIN_WORDS_WORK = _int("EN_MIN_WORDS_WORK", 50)
# "疑似名篇"判定：语言模型困惑度低于此值且分类器判为人写
# 中文段落困惑度（Qwen2.5-0.5B）低于此值：模型几乎逐字复现，视为公开名篇原文（《背影》实测 1.3–2.4），不计入 AI 率。
# 依据：评估集中约 1300 段中文 AI 文本（GLM / GPT-4o / Qwen / DeepSeek / 文心 / Kimi）困惑度最低 3.57。
FAMOUS_PPL_CLASSICAL = float(os.getenv("FAMOUS_PPL_CLASSICAL", "7.0") or 7.0)
FAMOUS_PPL_ZH = float(os.getenv("FAMOUS_PPL_ZH", "3.0") or 3.0)
# 名篇特征（整篇）：某段困惑度 < FAMOUS_WORK_PPL 且困惑度波动 > FAMOUS_WORK_BURST（模型对部分句子逐字背过、其余正常）。
# 评估集约 1460 段中文 AI 文本中只有 1 段同时满足（波动 > 0.9）；36 篇国产模型中文论文的结果不受影响；《草原》首段 5.5 / 0.94。
FAMOUS_WORK_PPL = float(os.getenv("FAMOUS_WORK_PPL", "6.0") or 6.0)
FAMOUS_WORK_BURST = float(os.getenv("FAMOUS_WORK_BURST", "0.9") or 0.9)
# 孤立段落：整篇像人写（中文分类器加权中位数 < 0.5）时，过线字数不足本篇此比例、且未达"高度疑似"的段落不计入
# （参照 Turnitin：AI 占比低于 20% 时不给具体数字，因为这一区间误判明显增多）
ISOLATED_MAX_SHARE = float(os.getenv("ISOLATED_MAX_SHARE", "0.25") or 0.25)
# 英文孤立段落保护（engine 7.5）：长篇英文作品整体像人写时，零星轻度过线的段落不计入
EN_ISOLATED_MIN_SEGS = _int("EN_ISOLATED_MIN_SEGS", 6)
EN_ISOLATED_WORK_EN2 = float(os.getenv("EN_ISOLATED_WORK_EN2", "0.3") or 0.3)
EN_ISOLATED_MAX_SHARE = float(os.getenv("EN_ISOLATED_MAX_SHARE", "0.2") or 0.2)
EN_ISOLATED_SEG_CAP = float(os.getenv("EN_ISOLATED_SEG_CAP", "0.95") or 0.95)
# 说明 / 操作类英文文字（engine 7.6）：祈使句比例 ≥ EN_INSTR_IMP 时，用更严门槛
# 学术摘要 / 学术文字（engine 7.7）
EN_ABS_DENSITY = float(os.getenv("EN_ABS_DENSITY", "0.04") or 0.04)
EN_ABS_EN2 = float(os.getenv("EN_ABS_EN2", "0.955") or 0.955)
EN_ABS_MIN_WORDS = int(os.getenv("EN_ABS_MIN_WORDS", "40") or 40)
EN_ABS_MAX_WORDS = int(os.getenv("EN_ABS_MAX_WORDS", "700") or 700)
EN_ABS_CLS_KEEP = float(os.getenv("EN_ABS_CLS_KEEP", "0.995") or 0.995)
EN_ABS_CLS_EN3 = float(os.getenv("EN_ABS_CLS_EN3", "0.90") or 0.90)
EN_ABS_NB = float(os.getenv("EN_ABS_NB", "0.88") or 0.88)
EN_ABS_P_KEEP = float(os.getenv("EN_ABS_P_KEEP", "0.99") or 0.99)
EN_INSTR_IMP = float(os.getenv("EN_INSTR_IMP", "0.25") or 0.25)
EN_INSTR_PROB = float(os.getenv("EN_INSTR_PROB", "0.90") or 0.90)
EN_INSTR_EN2 = float(os.getenv("EN_INSTR_EN2", "0.85") or 0.85)
EN_INSTR_EN3 = float(os.getenv("EN_INSTR_EN3", "0.90") or 0.90)
# 三个英文分类器都 ≥ EN_INSTR_ALL 且综合分数 ≥ EN_INSTR_ALL_PROB 时，说明类文字也计入（0 = 关闭；2026-11-04 试验中）
EN_INSTR_ALL = float(os.getenv("EN_INSTR_ALL", "0") or 0)
EN_INSTR_ALL_PROB = float(os.getenv("EN_INSTR_ALL_PROB", "0.6") or 0.6)
MEMORIZED_PPL = float(os.getenv("MEMORIZED_PPL", "3.5") or 3.5)
# 管理页校准时，用户样本与内置公开数据合并，用户样本合计所占的权重比例
USER_SAMPLE_SHARE = float(os.getenv("USER_SAMPLE_SHARE", "0.3") or 0.3)
USER_SAMPLE_MAX_WEIGHT = float(os.getenv("USER_SAMPLE_MAX_WEIGHT", "8") or 8)   # 每段用户标注最多抵几段内置样本
FAST_MODE_MAX_SEGMENTS = _int("FAST_MODE_MAX_SEGMENTS", 60)  # 快速模式下语言模型最多检测多少段
# 相邻段落平滑强度（0 = 不平滑，0.3 = 本段 70% + 相邻段 30%）
SMOOTHING = float(os.getenv("SMOOTHING", "0.3") or 0.3)
# 中文作品整篇判断：同一篇（≥3 段现代汉语正文）MPU 中文分类器得分的中位数（按字数加权）达到此值时，
# 本篇未过阈值的段落按"轻度疑似（整篇判断）"计入。验证（2026-10，线上服务实测）：77 篇知乎真人长回答中
# 未被判为 AI 的最高 0.79；DeepSeek / 文心 24 篇中文论文全部 ≥ 0.99，Kimi k3 6 篇里 5 篇 0.89–0.99，Claude 论文 0.90。
ZH_DOC_THRESHOLD = float(os.getenv("ZH_DOC_THRESHOLD", "0.88") or 0.88)
# 中文第二分类器整篇判断：同一篇中文作品各段得分按字数加权的中位数达到此值，本篇未过阈值的段落计为"中度疑似（整篇判断）"。
# 依据（chinese-classifier-v2，2026-10-04，含豆包 198 篇）：没参与训练的真人文档（文学散文、高考现代文、C3、知乎、网文、HC3）整篇中位数最高 0.19（v1 为 0.54），
# 用户的真人文章《背影》《草原》《废墟》《长征》约 0.05；五家国产模型 + Claude / ChatGPT / Gemini 写的文档整篇约 0.95。
# v5（2026-10-17，加入 ChatGPT / 豆包 / Claude 文白夹杂样本）：训练报告里没参与训练的真人文档整篇中位数最高 0.71（文学散文），
# AI 文档整篇约 0.95，所以阈值从 0.7 提到 0.85。
ZH2_DOC_THRESHOLD = float(os.getenv("ZH2_DOC_THRESHOLD", "0.85") or 0.85)
# 只有一段的中文短篇（≥ ZH2_SINGLE_MIN_CHARS 字）：第二分类器得分达到此值时按"中度疑似（整篇判断）"计入（见 engine 6.55）。
# 依据（2026-10-16，v3）：没参与训练的真人中文单段 2154 段（老舍、鲁迅、梁实秋、林语堂、钱钟书、周作人、蔡元培、胡适日记、
# 朱生豪书信、名家散文选等 11 部书各 140 段，学者论著 600 段，用户真人文档 14 段；每段 250–700 字）最高 0.41，没有一段 ≥ 0.5；
# 0 / 2154 的单侧 95% 上限约 0.14%。用户 AI 短篇《幸福的月光》《踏浪》0.96。
# v5 复测（同一批 2154 段）：学者论著 0/600、用户真人 0/8、名家书 9/1540（0.6%）达到 0.9；新的 ChatGPT 文白夹杂保留集
# 149/150 达到 0.9（v3 只有 49/150），1015.docx 的 5 篇 AI 现代文全部 ≥ 0.9（v3 2/5）。
# 2026-10-18 复核：v5 在完全没参与训练的 7 部真人书（名家散文、梁实秋、朱生豪等）1381 段里，21 段（1.5%）≥ 0.9、最高 0.94；
# AI（ChatGPT 文白夹杂保留集 148/150、1015.docx 5/5）≥ 0.95。阈值定为 0.95：这批真人 0 段达到。
ZH2_SINGLE_THRESHOLD = float(os.getenv("ZH2_SINGLE_THRESHOLD", "0.95") or 0.95)
ZH2_SINGLE_MIN_CHARS = _int("ZH2_SINGLE_MIN_CHARS", 250)
# 第二分类器多段合议（engine 6.575）：一篇里 ≥ ZH2_POOL_MIN_SEGS 段现代中文、各 ≥ ZH2_POOL_MIN_CHARS 字、得分都 ≥ ZH2_POOL_THRESHOLD 时一并计入。设为 0 关闭。
ZH2_POOL_MIN_SEGS = _int("ZH2_POOL_MIN_SEGS", 2)
ZH2_POOL_MIN_CHARS = _int("ZH2_POOL_MIN_CHARS", 80)
ZH2_POOL_THRESHOLD = float(os.getenv("ZH2_POOL_THRESHOLD", "0.945") or 0.945)
# 高分"引文"合议（engine 1.9）：同篇 ≥ QUOTE_POOL_MIN_SEGS 段引文得分 ≥ QUOTE_POOL_PROB、且占全部引文字数 ≥ QUOTE_POOL_SHARE 时改为计入。设为 0 关闭。
QUOTE_POOL_MIN_SEGS = _int("QUOTE_POOL_MIN_SEGS", 2)
# 现代文里的文言段落要有引用痕迹（引号、“曰 / 云”等引语、书名卷次）才按“古籍引文”不计入（2026-11-11 cs1111）
QUOTE_NEED_EVIDENCE = _bool("QUOTE_NEED_EVIDENCE", True)
QUOTE_UNCITED_PROB = float(os.getenv("QUOTE_UNCITED_PROB", "0.93") or 0.93)   # 无引用痕迹的文言引文，分数达到此值改为计入
# 同一段里文体切换：文言、诗词分开切；按约 REG_SPLIT_GROUP 个汉字一组判断文体（原为 36，切点偏差最多一组）
REG_SPLIT_POETRY = _bool("REG_SPLIT_POETRY", True)
REG_SPLIT_GROUP = _int("REG_SPLIT_GROUP", 24)
REG_SPLIT_REFINE = _bool("REG_SPLIT_REFINE", True)   # 现代文 / 文言交界处按句细调切点（见 segmenter._refine_reg_cuts）
QUOTE_POOL_MIN_CHARS = _int("QUOTE_POOL_MIN_CHARS", 40)
QUOTE_POOL_PROB = float(os.getenv("QUOTE_POOL_PROB", "0.95") or 0.95)
QUOTE_POOL_SHARE = float(os.getenv("QUOTE_POOL_SHARE", "0.25") or 0.25)
ZH2_SEG_MIN_CHARS = _int("ZH2_SEG_MIN_CHARS", 150)
ZH_AGREE_CLS = float(os.getenv("ZH_AGREE_CLS", "0.96") or 0.96)    # MPU 分类器 ≥ 此值且第二分类器 ≥ ZH_AGREE_ZH2 时按"中度疑似"计入（≥ 1 关闭）
ZH_AGREE_ZH2 = float(os.getenv("ZH_AGREE_ZH2", "0.9") or 0.9)
ZH_AGREE_MIN_CHARS = _int("ZH_AGREE_MIN_CHARS", 40)
CL_POOL_MEAN = float(os.getenv("CL_POOL_MEAN", "0.65") or 0.65)    # 文档内文言 / 诗词 ≥ 2 段的加权平均分达到此值时整组计入（≥ 1 关闭）   # 多段文章里单独一段按第二分类器计入的最短字数
EN_PAPER_DOC_THRESHOLD = float(os.getenv("EN_PAPER_DOC_THRESHOLD", "0.85") or 0.85)   # 英文论文整篇判断阈值（第二分类器中位数）
WORK_MAJORITY = float(os.getenv("WORK_MAJORITY", "0.6") or 0.6)   # 同篇已判 AI 的文字占比达到此值，接近阈值的段落按整篇计入
# 文言虚词（之乎者也矣焉哉曰…）占汉字比例超过此值的段落，视为以古籍引文为主，不计入 AI 率
# ---------- 逐句窗口（局部检测）----------
# 长段落再按句子切成约 100 字 / 70 词的窗口分别打分（参考 Turnitin：5–10 句的重叠窗口逐句给分）。用途：
#   ① 整段判为 AI、但其中有几句明显像人写（人写文章里插入了 AI 段落，或 AI 稿被人大幅改写）：只把 AI 的部分计入；
#   ② 整段未过阈值、但其中几句非常像 AI（人写文章里夹了几句 AI 生成或 AI 润色的句子）：只把这几句计入“局部疑似”。
WINDOWS_ENABLED = os.getenv("WINDOWS_ENABLED", "1") == "1"
WIN_ZH_CHARS = _int("WIN_ZH_CHARS", 100)
WIN_EN_WORDS = _int("WIN_EN_WORDS", 70)
WIN_AI_PROB = float(os.getenv("WIN_AI_PROB", "0.95") or 0.95)        # 窗口综合分数达到此值且第二分类器同意，算 AI 窗口
WIN_AI_SECOND = float(os.getenv("WIN_AI_SECOND", "0.9") or 0.9)
WIN_HUMAN_PROB = float(os.getenv("WIN_HUMAN_PROB", "0.15") or 0.15)  # 窗口综合分数低于此值且第二分类器也判人写，算人写窗口
WIN_HUMAN_SECOND = float(os.getenv("WIN_HUMAN_SECOND", "0.3") or 0.3)
WIN_ZH2_AI = float(os.getenv("WIN_ZH2_AI", "0.95") or 0.95)
WIN_ZH2_SEG = float(os.getenv("WIN_ZH2_SEG", "0.85") or 0.85)
WIN_PARTIAL_MIN_SHARE = float(os.getenv("WIN_PARTIAL_MIN_SHARE", "0.3") or 0.3)   # 人写窗口至少占整段这么多，才按“部分疑似”扣除
# 英文段落综合分数和第二分类器都 ≥ 0.95 时不做"部分疑似"扣除（2026-10-27）：用户 39 篇纯 AI 文档（ChatGPT / Claude）里，
# 112 段被按"部分句子像人写"打了折，其中 16 段两项都 ≥ 0.95，平均被扣掉 42%（如 ChatGPT《graph coloring》只剩 72%）；
# 16 本真人自我提升书被扣折的 11 段，综合分数最高 0.91、第二分类器最高 0.94，没有一段达到这条线。
# 整段两种检测器都极有把握时，单个短窗口的低分多半是短文本的噪声，不是插入的真人句子。
WIN_PARTIAL_SKIP_PROB = float(os.getenv("WIN_PARTIAL_SKIP_PROB", "0.95") or 0.95)
WIN_PARTIAL_SKIP_EN2 = float(os.getenv("WIN_PARTIAL_SKIP_EN2", "0.95") or 0.95)
COUPLET_CLASSIFIER_THRESHOLD = float(os.getenv("COUPLET_CLASSIFIER_THRESHOLD", "0.975") or 0.975)
CLASSICAL_THRESHOLD = float(os.getenv("CLASSICAL_THRESHOLD", "0.03") or 0.03)
MODERN_MAX_RATIO = float(os.getenv("MODERN_MAX_RATIO", "0.015") or 0.015)

# ---------- 限制 ----------
MAX_TEXT_CHARS = _int("MAX_TEXT_CHARS", 300_000)
SYNC_MAX_CHARS = _int("SYNC_MAX_CHARS", 3_000)       # 小于这个长度的请求直接同步返回
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 30)
MAX_QUEUED_JOBS = _int("MAX_QUEUED_JOBS", 20)
JOB_TTL_SECONDS = _int("JOB_TTL_SECONDS", 3600)

# ---------- API Key ----------
# KEY_SECRET：签发 Key 的签名密钥；ADMIN_TOKEN：管理页面密码。两者都应设为 Secret。
KEY_SECRET = os.getenv("KEY_SECRET", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
# 只接受经本站 Cloudflare Worker 转发的请求：Worker 在每个请求头里带上 X-ATC-Proxy（由 ADMIN_TOKEN 算出），
# 直接访问 Modal 地址的请求一律当作不存在（防止绕过登录、防止有人反复访问把服务叫醒、伪造来源 IP 猜管理员密码）。
# 部署工作流会设 REQUIRE_PROXY_SECRET=1；本地测试不设，照常可用。
REQUIRE_PROXY_SECRET = os.getenv("REQUIRE_PROXY_SECRET", "") == "1"
PROXY_SECRET = hmac.new(ADMIN_TOKEN.encode(), b"ai-text-checker/worker-proxy/v1", hashlib.sha256).hexdigest() if ADMIN_TOKEN else ""
REVOKED_KEY_IDS = {k.strip() for k in os.getenv("REVOKED_KEY_IDS", "").split(",") if k.strip()}
REQUIRE_KEY = _bool("REQUIRE_KEY", True)               # False = 网页和接口都无需 Key（不建议公开 Space 这样做）
DEFAULT_DAILY_CHARS = _int("DEFAULT_DAILY_CHARS", 1_000_000)

# ---------- 校准 ----------
# 可以直接把校准结果 JSON 填在 CALIBRATION_JSON 变量里；否则读 calibration.json；都没有就用内置默认值。
# 参考文献真实性核查（app/refcheck.py）：逐条到 Crossref / Semantic Scholar / arXiv 查是否存在，只作提示
REF_CHECK = _bool("REF_CHECK", True)
# 行内作品编号、同段中英文交替时先拆开再判断（segmenter.split_inline_labels / split_script_switches）
SPLIT_INLINE = _bool("SPLIT_INLINE", True)
# 人机混写切分（app/hybrid.py）：同一段里人写、AI 写的句子交替时，在作者疑似换了的地方切开、分别判断
# 默认关闭（2026-11-05 测试）：60 篇带答案的混写文章里只对英文“人写中插入一段 AI”有小幅提升（AI 字检出 50% → 60%），
# 中文几乎没有作用；200 + 91 篇真人文字被判 AI 的篇数不变，但平均 AI 率升了约 1 个百分点（零星句子被单独切出后过线）。
# 需要专门的句级模型后再开启。HYBRID_SPLIT=1 可做对照。
HYBRID_SPLIT = _bool("HYBRID_SPLIT", True)
# 逐句作者判断（2026-11-11 用户 cs1111：一整段里人写 / AI 写交替 47 次，不分段、不加标记）：长段落的每个句子单元用
# 句级标注模型 + 分类器打分，全段一起做两状态维特比解码（换作者有代价），按句子计入 AI 字数，代替整段一刀切。
# 依据：55 篇合成混写文档逐字评估——AI 字检出 35.5% → 49.8%，真人字误标 7.6% → 3.0%；纯真人文档误标 0.8% → 0.6%。
UNIT_HMM = _bool("UNIT_HMM", True)
UNIT_HMM_MIN_UNITS = _int("UNIT_HMM_MIN_UNITS", 8)
UNIT_HMM_MIN_MIX = _int("UNIT_HMM_MIN_MIX", 60)
# 语意衔接度调节换作者代价：> 0 时启用，数值为“正常衔接”的参考值（nats / 词元）；0 = 关闭（待诊断数据定参数）
UNIT_HMM_COH_MAX = float(os.getenv("UNIT_HMM_COH_MAX", "1.0"))   # 衔接度把换作者代价最多升到原来的几倍
UNIT_HMM_COH_MIN = float(os.getenv("UNIT_HMM_COH_MIN", "0.25"))   # 衔接度把换作者代价最多降到原来的几分之几（1 = 只升不降）
UNIT_HMM_COH_REGS = set(os.getenv("UNIT_HMM_COH_REGS", "zh,zh_classical,zh_poetry").replace(",", " ").split())   # 哪些文体用衔接度
UNIT_HMM_COH = float(os.getenv("UNIT_HMM_COH", "0.8") or 0)   # 段内人写、AI 都至少这么多字，才按逐句结果
UNIT_HMM_RESEGMENT = _bool("UNIT_HMM_RESEGMENT", True)
UNIT_HMM_WORKS = _bool("UNIT_HMM_WORKS", True)   # 同一首词 / 诗的句子绑在一起判断（app/structure.py）   # 报告按逐句判断的作者重新分段显示
UNIT_HMM_TH = os.getenv("UNIT_HMM_TH", "zh:0.8,zh_classical:0.85,en:0.85,zh_poetry:0.9")
UNIT_HMM_SURE_EN = float(os.getenv("UNIT_HMM_SURE_EN", "0.95") or 1)   # 英文句级标注模型 ≥ 此值的句子只按标注模型判断
# 换作者的位置对齐到现代文 / 文言切换处：一段 AI 句子开头或结尾不超过这么多字、与相邻人写部分同一文体的几句改回人写（0 关闭）
UNIT_HMM_REG_SNAP = int(os.getenv("UNIT_HMM_REG_SNAP", "150") or 0)
UNIT_HMM_SWITCH = float(os.getenv("UNIT_HMM_SWITCH", "0.5") or 0.5)
UNIT_HMM_ALPHA = float(os.getenv("UNIT_HMM_ALPHA", "0.3") or 0.3)   # 分类器所占权重（其余给句级标注模型）
UNIT_HMM_BETA_DOC = float(os.getenv("UNIT_HMM_BETA_DOC", "1.5"))
UNIT_HMM_BETA = float(os.getenv("UNIT_HMM_BETA", "0.5") or 0.5)     # 整段原判断作为先验的权重
# 网页提交的检测任务超过这么多秒没人查询进度（页面关了 / 取消了），自动取消，不再占着队列（0 = 不自动取消）
JOB_ABANDON_SECONDS = _int("JOB_ABANDON_SECONDS", 90)
# 只在该语言的句级标注模型可用时才切（2026-11-05：中文句级模型 chinese-tagger-v1 通过对照；英文没有句级模型时不切，
# 因为只用整段分类器拼句子分数时，真人英文平均分会升高约 1 分）
HYBRID_REQUIRE_TAGGER = _bool("HYBRID_REQUIRE_TAGGER", True)
# 文言 / 诗词也做混写切分（用专用分类器打短句分数，语言模型能逐字复现的名篇原文按人写）；2026-11-06 试验，默认关闭
HYBRID_CLASSICAL = _bool("HYBRID_CLASSICAL", False)
HYBRID_FAMOUS_PPL = float(os.getenv("HYBRID_FAMOUS_PPL", "4.0") or 4.0)
HYBRID_SIZE_WEIGHT = _bool("HYBRID_SIZE_WEIGHT", False)
HYBRID_SMOOTH_BARRIER = _bool("HYBRID_SMOOTH_BARRIER", True)   # 混写切分出的块不参与相邻平滑，也不让被它隔开的段落互相平滑（2026-11-07）
HYBRID_RELAX = _bool("HYBRID_RELAX", True)
HYBRID_NO_CARRY = _bool("HYBRID_NO_CARRY", True)   # 混写切分过的段落不再按整篇判断带上（2026-11-08 试验）   # 某块把握不足时把它并回邻居重算，而不是整段放弃切分（2026-11-08 试验）
HYBRID_CL_AI_MEAN = float(os.getenv("HYBRID_CL_AI_MEAN", "0.6") or 0.6)
# 文言 / 诗词的混写切分不放宽（2026-11-08：放宽后 4 篇纯 AI 拟古文被切出“人写”块，AI 率从 0.8–0.98 掉到 0.2–0.4）
HYBRID_CL_SWITCH_COST = float(os.getenv("HYBRID_CL_SWITCH_COST", "2.0") or 2.0)
HYBRID_CL_HUMAN_MEAN = float(os.getenv("HYBRID_CL_HUMAN_MEAN", "0.35") or 0.35)
# 一整段没有换行、现代汉语 / 文言 / 诗词首尾相接时，在文体切换处断开（segmenter.split_register_switches）；2026-11-06 试验
SPLIT_REGISTER = _bool("SPLIT_REGISTER", True)
# 同一段里夹着完整的词 / 近体诗：整首单独成段，不从作品中间切开（见 segmenter.split_work_spans）
SPLIT_WORKS = _bool("SPLIT_WORKS", True)
# 句级“作者标注”模型（tools/tagger_train.py 训练，Release english-tagger-v* / chinese-tagger-v*）：有它时人机混写切分用它的句子分数
TAGGER_EN_MODEL = os.getenv("TAGGER_EN_MODEL", "")
TAGGER_ZH_MODEL = os.getenv("TAGGER_ZH_MODEL", "")
TAGGER_ZC_MODEL = os.getenv("TAGGER_ZC_MODEL", "")   # 文言句级标注模型
TAGGER_ZP_MODEL = os.getenv("TAGGER_ZP_MODEL", "")   # 诗词句级标注模型
# 部署 / 离线验收按这里的 Release 名下载句级标注模型（空 = 不用）
TAGGER_ZH_ID = os.getenv("TAGGER_ZH_ID", "chinese-tagger-v1")
TAGGER_EN_ID = os.getenv("TAGGER_EN_ID", "english-tagger-v3")
TAGGER_ZC_ID = os.getenv("TAGGER_ZC_ID", "classical-tagger-v2")
TAGGER_ZP_ID = os.getenv("TAGGER_ZP_ID", "")
HYBRID_SWITCH_COST = float(os.getenv("HYBRID_SWITCH_COST", "1.0") or 1.0)   # 每换一次作者的代价（对数似然）
HYBRID_TEMP = float(os.getenv("HYBRID_TEMP", "1.5") or 1.5)
HYBRID_MIN_EN_WORDS = _int("HYBRID_MIN_EN_WORDS", 25)     # 切出来的一块至少这么长
HYBRID_MIN_ZH_CHARS = _int("HYBRID_MIN_ZH_CHARS", 40)
# 文言 / 诗词切分出的每一块至少多少字（2026-11-06，90 字：混写卷 AI 检出 50.5%→53.1%、真人误标 1.6%→1.2%；120 字则检出无提升。金史本传末尾 84 字被单独切出、判成 AI；文言句子短、证据少，单独切块要更长）
HYBRID_MIN_ZC_CHARS = _int("HYBRID_MIN_ZC_CHARS", 90)
HYBRID_AI_MEAN = float(os.getenv("HYBRID_AI_MEAN", "0.8") or 0.8)        # AI 块的单句分数平均至少这么高才切
HYBRID_HUMAN_MEAN = float(os.getenv("HYBRID_HUMAN_MEAN", "0.45") or 0.45)  # 人写块的平均至多这么高才切
HYBRID_AI_SURE = float(os.getenv("HYBRID_AI_SURE", "1.01") or 1.01)      # AI 块平均达到此值，综合分数没过阈值也计入（默认关闭：
# 2026-11-05 测试中短句的分类器分数整体偏高，这条让 91 篇真人说明文多误判 1 篇，只按完整流程的分数判断）
REF_CHECK_MAX = _int("REF_CHECK_MAX", 40)
REF_CHECK_BUDGET_S = float(os.getenv("REF_CHECK_BUDGET_S", "45") or 45)
CALIBRATION_FILE = Path(os.getenv("CALIBRATION_FILE", str(BASE_DIR / "calibration.json")))


def classifier_for(register: str) -> str:
    if register in ("en", "en_paper", "en_poetry"):
        # 用了第二分类器时，校准参数必须是按两个分类器一起拟合的，所以把两个名字一起记进校准的 models 里
        return f"{EN_CLASSIFIER_NAME}+{EN2_CLASSIFIER_ID}" if EN2_CLASSIFIER_MODEL else EN_CLASSIFIER_NAME
    if register == "zh_poetry" and POETRY_CLASSIFIER_MODEL:
        return POETRY_CLASSIFIER_ID
    if register == "zh_classical" and CLASSICAL_CLASSIFIER_MODEL:
        return CLASSICAL_CLASSIFIER_ID
    return CLASSIFIER_NAME


def _models_match(cal: dict, register: str) -> bool:
    m = cal.get("models") or {}
    if not m:
        return True
    return (m.get("observer"), m.get("performer"), m.get("classifier")) == (
        OBSERVER_MODEL, PERFORMER_MODEL, classifier_for(register))


def _filter_profiles(cal: dict) -> dict:
    """去掉与当前模型不一致的文体校准（换了模型，旧参数就不适用了）。"""
    profs = {k: v for k, v in (cal.get("profiles") or {}).items() if isinstance(v, dict) and _models_match(v, k)}
    out = dict(cal)
    if profs:
        out["profiles"] = profs
    else:
        out.pop("profiles", None)
    return out


def load_default_calibration():
    """随代码发布的默认校准（由 tools/evaluate.py 用公开数据集生成）。"""
    default = Path(__file__).resolve().parent / "default_calibration.json"
    if default.exists():
        try:
            cal = json.loads(default.read_text("utf-8"))
            cal = _filter_profiles(cal)
            if not _models_match(cal, "zh"):
                # 现代汉语部分不适用时，只保留仍适用的文体校准
                return ({"profiles": cal["profiles"]} if cal.get("profiles") else None)
            return cal
        except (OSError, json.JSONDecodeError):
            pass
    return None


# 管理页“用我的标注校准”后启用的各文体校准，永久保存在这个文件里（线上放在 Modal 持久卷上，服务重启后仍有效）。
# 只保存用户自己校准过的文体；其余文体始终跟随内置默认校准的更新。
USER_CALIBRATION_FILE = Path(os.getenv("USER_CALIBRATION_FILE", str(BASE_DIR / "user_calibration.json")))
CALIBRATION_VOLUME = os.getenv("CALIBRATION_VOLUME", "")


def load_user_profiles() -> dict:
    try:
        d = json.loads(USER_CALIBRATION_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {p: c for p, c in (d.get("profiles") or {}).items()
            if isinstance(c, dict) and _models_match(c, p)}


USER_LABELS_FILE = Path(os.getenv("USER_LABELS_FILE", str(BASE_DIR / "user_labels.json")))
AUTO_CALIBRATE_DELAY = float(os.getenv("AUTO_CALIBRATE_DELAY", "20"))   # 最后一次标注后等这么多秒再自动校准（连续标注只算一次）


def _commit_volume():
    if CALIBRATION_VOLUME:
        try:
            import modal
            modal.Volume.from_name(CALIBRATION_VOLUME).commit()
        except Exception:  # noqa: BLE001  提交失败时，容器正常退出时 Modal 也会自动提交
            pass


def load_json_file(path: Path, default):
    try:
        return json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json_file(path: Path, obj) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
    except OSError:
        return False
    _commit_volume()
    return True


def save_user_profiles(profiles: dict) -> bool:
    """写入并提交到持久卷。返回是否成功永久保存。"""
    return save_json_file(USER_CALIBRATION_FILE, {"format": "user_profiles_v1", "profiles": profiles})


def load_calibration_override():
    """管理员自己的校准：环境变量 CALIBRATION_JSON 优先，其次 calibration.json。"""
    raw = os.getenv("CALIBRATION_JSON", "").strip()
    if raw:
        try:
            return _filter_profiles(json.loads(raw)), "环境变量 CALIBRATION_JSON"
        except json.JSONDecodeError:
            pass
    if CALIBRATION_FILE.exists():
        try:
            return _filter_profiles(json.loads(CALIBRATION_FILE.read_text("utf-8"))), f"文件 {CALIBRATION_FILE.name}"
        except (OSError, json.JSONDecodeError):
            pass
    return None, None

# 英文短条目清单（配料表、核对清单）不计入 AI 率（2026-11-03 实验：Claude 菜谱 8/18 漏判主要因配料表段落拉低）
EN_LIST_EXCLUDE = os.getenv("EN_LIST_EXCLUDE", "1") == "1"
