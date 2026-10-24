# 检测效果评估报告

每种文体各自校准：先识别段落是现代汉语、文言还是英文，再用对应的分类器和阈值判断。
语言模型：Qwen/Qwen2.5-0.5B + Qwen/Qwen2.5-0.5B-Instruct；中文分类器 yuchuantian/AIGC_detector_zhv3；英文分类器 desklib/ai-text-detector-v1.01。

“检出率”= AI 文本被判为 AI 的比例；“误判率”= 人写文本被误判为 AI 的比例；AUROC 1 为完美区分，0.5 为随机。

## 现代汉语
- 数据：NLPCC 2025 Task 1（CSL 学术摘要 / 新闻 / 作文；GPT-4o、GLM-4、Qwen）
- 校准集 400 人写 / 396 AI；阈值 0.5；交叉验证 AUROC 0.9997；特征 fastdetect, binoculars, logit_classifier, fastdetect_norm, lrr, log_rank, entropy, top10, lp_burstiness, style_cv, style_phrases
- 特征组合比较（校准集交叉验证 AUROC）：全部特征 0.9997，三个主信号 0.9999，语言模型特征 0.9789，全部特征 + 惊奇度多样性（DivEye） 0.9997 → 选用全部特征
- **NLPCC 测试集（训练时未见，含 DeepSeek-V3）**（200 AI / 200 人写）：AUROC 0.9742；检出率 85.5%；误判率 2.0%
  - 各特征 AUROC：fastdetect 0.8471，binoculars 0.1516，logit_classifier 0.9728，fastdetect_norm 0.8464，lrr 0.8495，log_rank 0.1736，entropy 0.2235，top10 0.8559，lp_burstiness 0.8478，style_cv 0.2175，style_phrases 0.5068
- **CSL 学术摘要保留集**（180 AI / 60 人写）：AUROC 1.0；检出率 100.0%；误判率 0.0%
  - 各来源检出率：glm 100%，gpt4o 100%，qwen 100%
  - 各来源误判率：human 0%
  - 各特征 AUROC：fastdetect 0.9662，binoculars 0.0362，logit_classifier 0.9997，fastdetect_norm 0.9639，lrr 0.978，log_rank 0.0306，entropy 0.1474，top10 0.9769，lp_burstiness 0.5993，style_cv 0.2154，style_phrases 0.8452

## 现代汉语短段
- 数据：NLPCC 2025 Task 1 样本截成 80–260 字的短段
- 校准集 362 人写 / 369 AI；阈值 0.7014；交叉验证 AUROC 0.9976；特征 fastdetect, binoculars, logit_classifier, fastdetect_norm, lrr, log_rank, entropy, top10, lp_burstiness, style_cv, style_phrases
- 特征组合比较（校准集交叉验证 AUROC）：全部特征 0.9976，三个主信号 0.9971，语言模型特征 0.9419，全部特征 + 惊奇度多样性（DivEye） 0.9973 → 选用全部特征
- **NLPCC 测试集截成 80–260 字的短段（含本仓库 AI 读后感 / 散文）**（207 AI / 186 人写）：AUROC 0.9277；检出率 76.3%；误判率 3.2%
  - 各来源检出率：repo-ai-zh-essay 96%
  - 各特征 AUROC：fastdetect 0.8092，binoculars 0.1899，logit_classifier 0.9268，fastdetect_norm 0.8072，lrr 0.7227，log_rank 0.2568，entropy 0.3665，top10 0.7556，lp_burstiness 0.7095，style_cv 0.344，style_phrases 0.5053

## 英文
- 数据：MAGE（人写文本与 GPT-3.5 / GPT-4 等生成文本）
- 校准集 1232 人写 / 800 AI；阈值 0.7435；交叉验证 AUROC 0.9855；特征 fastdetect, binoculars, logit_classifier, logit_classifier_en2
- **国产大模型英文论文段落（DeepSeek / 文心一言写的摘要、引言、结果、结论）vs 同题 arXiv 真人摘要；题目没参与训练和校准**（548 AI / 478 人写）：AUROC 0.9874；检出率 87.4%；误判率 4.4%
  - 各来源误判率：arxiv-human 11%，genre-human 2%，pmc-human 0%，pubmed-human 6%
  - 各特征 AUROC：fastdetect 0.63，binoculars 0.3669，logit_classifier 0.8974，fastdetect_norm 0.631，lrr 0.5666，log_rank 0.3848，entropy 0.4029，top10 0.6046，lp_burstiness 0.5537，style_cv 0.5339，style_phrases 0.5878，logit_classifier_en2 0.9978
- **国产新模型 AI 英文短篇（DeepSeek / Kimi / 文心一言，用户提供；英文第二分类器训练时没见过的那 1/3）**（15 AI / 0 人写）：AUROC None；检出率 80.0%
  - 各来源检出率：repo-ai-deepseek 90%，repo-ai-kimi 0%，repo-ai-wenxin 100%
  - 各特征 AUROC：
- **当代出版小说（真人，没参与校准的那一半书）与 AI 写的小说段落**（47 AI / 616 人写）：AUROC 0.9864；检出率 70.2%；误判率 1.8%
  - 各来源检出率：claude-ai 77%，cs1021-ai 59%
  - 各来源误判率：novel-human 1%，novel-human-sparks 8%，cs1021-human 12%
  - 各特征 AUROC：fastdetect 0.7359，binoculars 0.2868，logit_classifier 0.8631，fastdetect_norm 0.7182，lrr 0.7338，log_rank 0.242，entropy 0.2837，top10 0.753，lp_burstiness 0.508，style_cv 0.4014，style_phrases 0.5564，logit_classifier_en2 0.9966
- **MAGE：GPT-4 在未见过的领域生成的文本**（150 AI / 150 人写）：AUROC 0.9879；检出率 95.3%；误判率 12.0%
  - 各来源检出率：cnn_gpt4 94%，imdb_gpt4 94%，pubmed_gpt4 97%，dialogsum_gpt4 97%
  - 各来源误判率：pubmed_human 12%，dialogsum_human 32%，imdb_human 4%，cnn_human 0%
  - 各特征 AUROC：fastdetect 0.8273，binoculars 0.1768，logit_classifier 0.9828，fastdetect_norm 0.8235，lrr 0.7548，log_rank 0.1979，entropy 0.2756，top10 0.7896，lp_burstiness 0.7117，style_cv 0.3014，style_phrases 0.6956，logit_classifier_en2 0.9685
- **MAGE：GPT-4 文本经改写后（含本仓库英文 AI 样本）**（173 AI / 150 人写）：AUROC 0.8868；检出率 74.6%；误判率 13.3%
  - 各来源检出率：pubmed_gpt4_para 53%，cnn_gpt4_para 72%，imdb_gpt4_para 87%，dialogsum_gpt4_para 85%，repo-ai-english 96%
  - 各来源误判率：pubmed_human 5%，imdb_human 0%，pubmed_human_para 6%，cnn_human 0%，dialogsum_human_para 57%，cnn_human_para 0%，imdb_human_para 18%，dialogsum_human 15%
  - 各特征 AUROC：fastdetect 0.5799，binoculars 0.4234，logit_classifier 0.8832，fastdetect_norm 0.5805，lrr 0.6235，log_rank 0.3531，entropy 0.3639，top10 0.6535，lp_burstiness 0.5053，style_cv 0.3114，style_phrases 0.5934，logit_classifier_en2 0.8432
- **国产大模型英文论文段落（DeepSeek / 文心一言写的摘要、引言、结果、结论）vs 同题 arXiv 真人摘要；题目没参与训练和校准【对照：不用英文第二分类器】**（548 AI / 478 人写）：AUROC 0.8981；检出率 70.8%；误判率 3.4%
  - 各来源误判率：arxiv-human 5%，genre-human 2%，pmc-human 3%，pubmed-human 4%
  - 各特征 AUROC：fastdetect 0.63，binoculars 0.3669，logit_classifier 0.8974，fastdetect_norm 0.631，lrr 0.5666，log_rank 0.3848，entropy 0.4029，top10 0.6046，lp_burstiness 0.5537，style_cv 0.5339，style_phrases 0.5878，logit_classifier_en2 0.9978
- **国产新模型 AI 英文短篇（DeepSeek / Kimi / 文心一言，用户提供；英文第二分类器训练时没见过的那 1/3）【对照：不用英文第二分类器】**（15 AI / 0 人写）：AUROC None；检出率 13.3%
  - 各来源检出率：repo-ai-deepseek 10%，repo-ai-kimi 0%，repo-ai-wenxin 33%
  - 各特征 AUROC：
- **当代出版小说（真人，没参与校准的那一半书）与 AI 写的小说段落【对照：不用英文第二分类器】**（47 AI / 616 人写）：AUROC 0.8488；检出率 51.1%；误判率 2.8%
  - 各来源检出率：claude-ai 57%，cs1021-ai 41%
  - 各来源误判率：novel-human 1%，novel-human-sparks 17%，cs1021-human 6%
  - 各特征 AUROC：fastdetect 0.7359，binoculars 0.2868，logit_classifier 0.8631，fastdetect_norm 0.7182，lrr 0.7338，log_rank 0.242，entropy 0.2837，top10 0.753，lp_burstiness 0.508，style_cv 0.4014，style_phrases 0.5564，logit_classifier_en2 0.9966
- **MAGE：GPT-4 在未见过的领域生成的文本【对照：不用英文第二分类器】**（150 AI / 150 人写）：AUROC 0.9824；检出率 95.3%；误判率 6.0%
  - 各来源检出率：cnn_gpt4 91%，imdb_gpt4 96%，pubmed_gpt4 94%，dialogsum_gpt4 100%
  - 各来源误判率：pubmed_human 6%，dialogsum_human 18%，imdb_human 0%，cnn_human 0%
  - 各特征 AUROC：fastdetect 0.8273，binoculars 0.1768，logit_classifier 0.9828，fastdetect_norm 0.8235，lrr 0.7548，log_rank 0.1979，entropy 0.2756，top10 0.7896，lp_burstiness 0.7117，style_cv 0.3014，style_phrases 0.6956，logit_classifier_en2 0.9685
- **MAGE：GPT-4 文本经改写后（含本仓库英文 AI 样本）【对照：不用英文第二分类器】**（173 AI / 150 人写）：AUROC 0.8846；检出率 74.6%；误判率 14.0%
  - 各来源检出率：pubmed_gpt4_para 88%，cnn_gpt4_para 60%，imdb_gpt4_para 81%，dialogsum_gpt4_para 74%，repo-ai-english 65%
  - 各来源误判率：pubmed_human 11%，imdb_human 0%，pubmed_human_para 0%，cnn_human 6%，dialogsum_human_para 39%，cnn_human_para 24%，imdb_human_para 24%，dialogsum_human 0%
  - 各特征 AUROC：fastdetect 0.5799，binoculars 0.4234，logit_classifier 0.8832，fastdetect_norm 0.5805，lrr 0.6235，log_rank 0.3531，entropy 0.3639，top10 0.6535，lp_burstiness 0.5053，style_cv 0.3114，style_phrases 0.5934，logit_classifier_en2 0.8432

## 英文学术论文
- 数据：arXiv / PubMed / PMC 真人学术英文（含中国作者）+ DeepSeek / 文心一言 / Kimi 写的英文论文
- 校准集 480 人写 / 500 AI；阈值 0.941；交叉验证 AUROC 0.9891；特征 logit_classifier_en2, fastdetect, binoculars
- **英文学术论文：没参与训练和校准的题目（国产模型写的论文 vs arXiv / PubMed / PMC 真人，含中国作者）**（548 AI / 478 人写）：AUROC 0.9916；检出率 94.0%；误判率 2.7%
  - 各来源误判率：arxiv-human 5%，genre-human 2%，pmc-human 0%，pubmed-human 4%
  - 各特征 AUROC：fastdetect 0.63，binoculars 0.3669，logit_classifier 0.8974，fastdetect_norm 0.631，lrr 0.5666，log_rank 0.3848，entropy 0.4029，top10 0.6046，lp_burstiness 0.5537，style_cv 0.5339，style_phrases 0.5878，logit_classifier_en2 0.9978
- **国产新模型 AI 英文短篇（用户提供，没参与训练的那 1/3）**（15 AI / 0 人写）：AUROC None；检出率 66.7%
  - 各来源检出率：repo-ai-deepseek 60%，repo-ai-kimi 100%，repo-ai-wenxin 67%
  - 各特征 AUROC：

## 文言
- 数据：NiuTrans 古文语料（人写）+ 大语言模型生成的文言样本
- 校准集 373 人写 / 198 AI；阈值 0.5；交叉验证 AUROC 0.9999；特征 fastdetect, binoculars, logit_classifier
- **文言保留集（另一组古籍 + 未参与校准的 AI 文言）**（147 AI / 278 人写）：AUROC 0.9988；检出率 99.3%；误判率 2.2%
  - 各来源检出率：llm-classical 98%，gen-claude 100%，gen-deepseek 100%，gen-doubao 100%，gen-kimi 100%，gen-qwen 100%
  - 各特征 AUROC：fastdetect 0.8286，binoculars 0.1873，logit_classifier 0.9987，fastdetect_norm 0.815，lrr 0.792，log_rank 0.1741，entropy 0.2699，top10 0.8004，lp_burstiness 0.5278，style_cv 0.4399，style_phrases 0.5016，logit_classifier_mpu 0.8773
- **国产新模型 AI 文言故事（DeepSeek / Kimi / 文心一言；不参与校准，但其中 2/3 用于训练文言分类器，没见过的那 1/3 见分类器训练报告）**（50 AI / 0 人写）：AUROC None；检出率 100.0%
  - 各来源检出率：repo-ai-deepseek 100%，repo-ai-kimi 100%，repo-ai-wenxin 100%
  - 各特征 AUROC：

## 诗词
- 数据：ChangAn 当代旧体诗词（人写）+ DeepSeek / 豆包 / GPT-4.1 生成诗词；诗词专用分类器（ChangAn 训练集微调）
- 校准集 400 人写 / 399 AI；阈值 0.8481；交叉验证 AUROC 0.9607；特征 logit_classifier
- 特征组合比较（四种独立对照的 AUROC，按最差情况选）：
  - 只用诗词分类器：ChangAn 保留集 0.9543，故事诗 vs 当代人写 0.6536，ChangAn AI vs 唐宋名篇 0.9641，故事诗 vs 唐宋名篇 0.6805，国产新模型诗 vs 当代人写 0.9714，国产新模型诗 vs 唐宋名篇 0.9771（最差 0.6536）
  - 诗词分类器 + 语言模型：ChangAn 保留集 0.9513，故事诗 vs 当代人写 0.7208，ChangAn AI vs 唐宋名篇 0.7903，故事诗 vs 唐宋名篇 0.3831，国产新模型诗 vs 当代人写 0.9878，国产新模型诗 vs 唐宋名篇 0.8844（最差 0.3831）
  - 诗词分类器 + 通用分类器 + 语言模型：ChangAn 保留集 0.9522，故事诗 vs 当代人写 0.6657，ChangAn AI vs 唐宋名篇 0.7616，故事诗 vs 唐宋名篇 0.3434，国产新模型诗 vs 当代人写 0.9746，国产新模型诗 vs 唐宋名篇 0.8067（最差 0.3434）
  - 通用分类器 + 语言模型：ChangAn 保留集 0.8769，故事诗 vs 当代人写 0.5923，ChangAn AI vs 唐宋名篇 0.3359，故事诗 vs 唐宋名篇 0.1325，国产新模型诗 vs 当代人写 0.8361，国产新模型诗 vs 唐宋名篇 0.3113（最差 0.1325）
  - 只用语言模型：ChangAn 保留集 0.8379，故事诗 vs 当代人写 0.6759，ChangAn AI vs 唐宋名篇 0.2267，故事诗 vs 唐宋名篇 0.1128，国产新模型诗 vs 当代人写 0.9366，国产新模型诗 vs 唐宋名篇 0.3101（最差 0.1128）
- 选用：只用诗词分类器
- **国产新模型 AI 诗词（DeepSeek / Kimi / 文心一言，用户提供，不参与校准）**（70 AI / 0 人写）：AUROC None；检出率 81.4%
  - 各来源检出率：repo-ai-deepseek 83%，repo-ai-kimi 75%，repo-ai-wenxin 87%
  - 各特征 AUROC：
- **ChangAn 保留集（另一批作者 + 没见过的 Kimi-K2 与其他模型的新诗词）**（300 AI / 300 人写）：AUROC 0.9543；检出率 67.3%；误判率 3.0%
  - 各来源检出率：Deepseek 61%，gpt-4.1 79%，kimi-k2 56%，seed 73%
  - 各来源误判率：human 3%
  - 各特征 AUROC：fastdetect 0.7595，binoculars 0.2502，logit_classifier 0.9543，fastdetect_norm 0.7519，lrr 0.8175，log_rank 0.1699，entropy 0.2499，top10 0.7724，lp_burstiness 0.5953，style_cv 0.4707，style_phrases 0.5，logit_classifier_mpu 0.7237
- **复述故事情节的 AI 诗词（本仓库自带，40 首）**（40 AI / 0 人写）：AUROC None；检出率 7.5%
  - 各来源检出率：repo-ai-story-poem 8%
  - 各特征 AUROC：
- **唐诗三百首 + 宋词三百首（人写名篇，检查误判）**（0 AI / 646 人写）：AUROC None；误判率 2.2%
  - 各来源误判率：唐诗三百首 2%，宋词三百首 2%
  - 各特征 AUROC：
