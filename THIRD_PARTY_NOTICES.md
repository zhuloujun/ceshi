# 第三方组件与许可证清单（THIRD PARTY NOTICES）

审读（本仓库）自己编写的代码、自己训练的模型权重和校准参数归项目作者所有。下面列出项目用到的外部模型、数据、代码库和服务，
以及各自许可证带来的义务。**修改或再训练不会取消原许可证**：基于 Apache-2.0 / MIT 模型训练出来的模型仍要保留原作者的版权声明和许可证文本。

整理日期：2026-11-05。许可证以各项目页面当时的标注为准，商业化前应再核对一次（作者不是律师，本清单不构成法律意见）。

## 一、线上检测服务直接运行的模型

| 组件 | 作用 | 许可证 | 可商用 | 义务 |
|---|---|---|---|---|
| Qwen/Qwen2.5-0.5B、Qwen2.5-0.5B-Instruct（阿里通义） | 零样本检测（Binoculars / Fast-DetectGPT 的观察者 / 执行者） | Apache-2.0 | 是 | 保留版权与许可证声明 |
| yuchuantian/AIGC_detector_zhv3（华为诺亚，Tian 等，MPU） | 中文第一分类器 | Apache-2.0（代码仓库 AIGC_text_detector；zhv2 模型卡同为 Apache-2.0，zhv3 模型卡需再确认） | 是 | 保留声明 |
| desklib/ai-text-detector-v1.01 | 英文第一分类器 | MIT（基座 microsoft/deberta-v3-large，MIT） | 是 | 保留版权声明 |
| 本项目训练的分类器：chinese-classifier-*、english-classifier-*、poetry-classifier-*、classical-classifier-* | 中英文第二分类器、诗词、文言 | 本项目所有；基座为 hfl/chinese-roberta-wwm-ext（Apache-2.0）、FacebookAI/roberta-base（MIT） | 是 | 基座的版权声明随模型一起保留 |

## 二、检测方法（论文公式，代码为本项目自行实现）

| 方法 | 论文 | 官方代码许可证 |
|---|---|---|
| Binoculars | Hans 等，ICML 2024 | BSD-3-Clause（ahans30/Binoculars） |
| Fast-DetectGPT | Bao 等，ICLR 2024 | MIT（baoguangsheng/fast-detect-gpt） |
| MPU（正例–无标注学习） | Tian 等，ICLR 2024 | Apache-2.0 |

公式和思路属于公开的学术成果，本项目没有复制它们的源代码。

## 三、训练、校准与评估数据

只用于训练 / 校准 / 测试，**不随线上服务分发**。

| 数据 | 许可证 / 状态 | 风险 |
|---|---|---|
| MAGE（yaful/MAGE） | Apache-2.0 | 低 |
| ChangAn 诗词（VelikayaScarlet/ChangAn） | README 标注 MIT（仓库里没有 LICENSE 文件） | 低–中 |
| chinese-poetry（古典诗词原文） | MIT；原文属公有领域 | 低 |
| GAOKAO-Bench | Apache-2.0 | 低 |
| HC3-Chinese | CC-BY-SA-4.0 | 低（需署名） |
| 维基百科 / wiki40b | CC-BY-SA | 低（需署名） |
| arXiv 摘要（元数据） | CC0 | 低 |
| PubMed 摘要、PMC 全文 | 各论文版权不同（PMC 开放获取子集多为 CC-BY，部分 CC-BY-NC） | 中 |
| 古腾堡电子书 | 美国公有领域 | 低 |
| NLPCC-2025-Task1 | 仓库未标许可证（默认保留所有权利，供评测使用） | 中 |
| RAID、WritingPrompts、CC-News、IvyPanda 论文、IMDB、CNN/DailyMail、知乎 KOL、网络小说、对联数据集、Classical-Modern | 来源平台内容，版权归原作者，多数未明确授权商用 | 中–高 |
| BookCorpusOpen、用户提供的电子书、当代小说、古籍整理本 | 受版权保护 | 高：只以加密文件保存在 data-1020 分支，不进公开代码 |
| 各平台 AI 生成文本（DeepSeek、Kimi、千问、豆包、文心、ChatGPT、Claude 等） | 生成内容按各平台服务条款归调用者使用 | 低 |

## 四、代码库与前端

| 组件 | 许可证 |
|---|---|
| PyTorch、Transformers、tokenizers、huggingface_hub | BSD-3 / Apache-2.0 |
| FastAPI、uvicorn、numpy、pdfminer.six、reportlab、pypinyin、python-multipart、sentencepiece、protobuf | MIT / BSD / Apache-2.0 |
| pdf.js（前端读取 PDF） | Apache-2.0 |
| mammoth.js（前端读取 .docx） | BSD-2-Clause |
| JSZip | MIT（或 GPLv3，二选一，本项目选 MIT） |
| 文泉驿正黑（PDF 报告字体） | GPLv2 + 字体嵌入例外（嵌入 PDF 不使文档受 GPL 约束） |

## 五、外部服务

Cloudflare Workers / D1、Modal、GitHub Actions、Kaggle、Resend，以及 DeepSeek、Kimi、千问、豆包、文心等 API：按各自服务条款付费 / 使用，不涉及代码许可证。

---

Apache-2.0 与 MIT 许可证全文见 https://www.apache.org/licenses/LICENSE-2.0 与 https://opensource.org/license/mit 。
