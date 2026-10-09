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
- 校准集 776 人写 / 655 AI；阈值 0.7444；交叉验证 AUROC 0.9793；特征 fastdetect, binoculars, logit_classifier, logit_classifier_en2
- **国产大模型英文论文段落（DeepSeek / 文心一言写的摘要、引言、结果、结论）vs 同题 arXiv 真人摘要；题目没参与训练和校准**（399 AI / 478 人写）：AUROC 0.977；检出率 82.7%；误判率 2.3%
  - 各来源误判率：arxiv-human 5%，genre-human 4%，pmc-human 0%，pubmed-human 1%
  - 各特征 AUROC：fastdetect 0.6862，binoculars 0.3144，logit_classifier 0.9338，fastdetect_norm 0.6872，lrr 0.6475，log_rank 0.2981，entropy 0.3194，top10 0.6914，lp_burstiness 0.5916，style_cv 0.5131，style_phrases 0.5963，logit_classifier_en2 0.9746
- **国产新模型 AI 英文短篇（DeepSeek / Kimi / 文心一言，用户提供；英文第二分类器训练时没见过的那 1/3）**（15 AI / 0 人写）：AUROC None；检出率 20.0%
  - 各来源检出率：repo-ai-deepseek 20%，repo-ai-kimi 0%，repo-ai-wenxin 33%
  - 各特征 AUROC：
- **MAGE：GPT-4 在未见过的领域生成的文本**（150 AI / 150 人写）：AUROC 0.9775；检出率 94.7%；误判率 8.7%
  - 各来源检出率：cnn_gpt4 97%，imdb_gpt4 96%，pubmed_gpt4 86%，dialogsum_gpt4 100%
  - 各来源误判率：pubmed_human 6%，dialogsum_human 18%，imdb_human 0%，cnn_human 10%
  - 各特征 AUROC：fastdetect 0.8273，binoculars 0.1768，logit_classifier 0.9828，fastdetect_norm 0.8235，lrr 0.7548，log_rank 0.1979，entropy 0.2756，top10 0.7896，lp_burstiness 0.7117，style_cv 0.3014，style_phrases 0.6956，logit_classifier_en2 0.9213
- **MAGE：GPT-4 文本经改写后（含本仓库英文 AI 样本）**（173 AI / 150 人写）：AUROC 0.8506；检出率 64.2%；误判率 12.7%
  - 各来源检出率：pubmed_gpt4_para 35%，cnn_gpt4_para 63%，imdb_gpt4_para 77%，dialogsum_gpt4_para 85%，repo-ai-english 87%
  - 各来源误判率：pubmed_human 0%，imdb_human 0%，pubmed_human_para 0%，cnn_human 24%，dialogsum_human_para 39%，cnn_human_para 10%，imdb_human_para 18%，dialogsum_human 8%
  - 各特征 AUROC：fastdetect 0.5799，binoculars 0.4234，logit_classifier 0.8832，fastdetect_norm 0.5805，lrr 0.6235，log_rank 0.3531，entropy 0.3639，top10 0.6535，lp_burstiness 0.5053，style_cv 0.3114，style_phrases 0.5934，logit_classifier_en2 0.7491
- **国产大模型英文论文段落（DeepSeek / 文心一言写的摘要、引言、结果、结论）vs 同题 arXiv 真人摘要；题目没参与训练和校准【对照：不用英文第二分类器】**（399 AI / 478 人写）：AUROC 0.932；检出率 76.9%；误判率 2.7%
  - 各来源误判率：arxiv-human 5%，genre-human 2%，pmc-human 2%，pubmed-human 3%
  - 各特征 AUROC：fastdetect 0.6862，binoculars 0.3144，logit_classifier 0.9338，fastdetect_norm 0.6872，lrr 0.6475，log_rank 0.2981，entropy 0.3194，top10 0.6914，lp_burstiness 0.5916，style_cv 0.5131，style_phrases 0.5963，logit_classifier_en2 0.9746
- **国产新模型 AI 英文短篇（DeepSeek / Kimi / 文心一言，用户提供；英文第二分类器训练时没见过的那 1/3）【对照：不用英文第二分类器】**（15 AI / 0 人写）：AUROC None；检出率 6.7%
  - 各来源检出率：repo-ai-deepseek 0%，repo-ai-kimi 0%，repo-ai-wenxin 33%
  - 各特征 AUROC：
- **MAGE：GPT-4 在未见过的领域生成的文本【对照：不用英文第二分类器】**（150 AI / 150 人写）：AUROC 0.9841；检出率 93.3%；误判率 6.0%
  - 各来源检出率：cnn_gpt4 86%，imdb_gpt4 94%，pubmed_gpt4 94%，dialogsum_gpt4 100%
  - 各来源误判率：pubmed_human 6%，dialogsum_human 18%，imdb_human 0%，cnn_human 0%
  - 各特征 AUROC：fastdetect 0.8273，binoculars 0.1768，logit_classifier 0.9828，fastdetect_norm 0.8235，lrr 0.7548，log_rank 0.1979，entropy 0.2756，top10 0.7896，lp_burstiness 0.7117，style_cv 0.3014，style_phrases 0.6956，logit_classifier_en2 0.9213
- **MAGE：GPT-4 文本经改写后（含本仓库英文 AI 样本）【对照：不用英文第二分类器】**（173 AI / 150 人写）：AUROC 0.8855；检出率 69.9%；误判率 13.3%
  - 各来源检出率：pubmed_gpt4_para 80%，cnn_gpt4_para 58%，imdb_gpt4_para 74%，dialogsum_gpt4_para 74%，repo-ai-english 61%
  - 各来源误判率：pubmed_human 11%，imdb_human 0%，pubmed_human_para 0%，cnn_human 6%，dialogsum_human_para 39%，cnn_human_para 24%，imdb_human_para 18%，dialogsum_human 0%
  - 各特征 AUROC：fastdetect 0.5799，binoculars 0.4234，logit_classifier 0.8832，fastdetect_norm 0.5805，lrr 0.6235，log_rank 0.3531，entropy 0.3639，top10 0.6535，lp_burstiness 0.5053，style_cv 0.3114，style_phrases 0.5934，logit_classifier_en2 0.7491

## 英文学术论文
- 数据：arXiv / PubMed / PMC 真人学术英文（含中国作者）+ DeepSeek / 文心一言 / Kimi 写的英文论文
- 校准集 480 人写 / 355 AI；阈值 0.891；交叉验证 AUROC 0.9799；特征 logit_classifier_en2, fastdetect, binoculars
- **英文学术论文：没参与训练和校准的题目（国产模型写的论文 vs arXiv / PubMed / PMC 真人，含中国作者）**（399 AI / 478 人写）：AUROC 0.9709；检出率 73.7%；误判率 1.1%
  - 各来源误判率：arxiv-human 3%，genre-human 2%，pmc-human 0%，pubmed-human 0%
  - 各特征 AUROC：fastdetect 0.6862，binoculars 0.3144，logit_classifier 0.9338，fastdetect_norm 0.6872，lrr 0.6475，log_rank 0.2981，entropy 0.3194，top10 0.6914，lp_burstiness 0.5916，style_cv 0.5131，style_phrases 0.5963，logit_classifier_en2 0.9746
- **国产新模型 AI 英文短篇（用户提供，没参与训练的那 1/3）**（15 AI / 0 人写）：AUROC None；检出率 33.3%
  - 各来源检出率：repo-ai-deepseek 50%，repo-ai-kimi 0%，repo-ai-wenxin 0%
  - 各特征 AUROC：

## 文言
- 数据：NiuTrans 古文语料（人写）+ 大语言模型生成的文言样本
- 校准集 236 人写 / 80 AI；阈值 0.5；交叉验证 AUROC 0.9994；特征 fastdetect, binoculars, logit_classifier
- **文言保留集（另一组古籍 + 未参与校准的 AI 文言）**（40 AI / 143 人写）：AUROC 0.9888；检出率 92.5%；误判率 2.1%
  - 各来源检出率：llm-classical 92%
  - 各来源误判率：入蜀记 0%，唐传奇 0%，困学纪闻 0%，幽明录 0%，搜神记 0%，新唐书 9%，旧五代史 0%，明夷待访录 0%，武林旧事 0%，聊斋志异 0%，西湖梦寻 18%，资治通鉴 0%，金史 0%，陶庵梦忆 0%
  - 各特征 AUROC：fastdetect 0.8934，binoculars 0.1028，logit_classifier 0.9895，fastdetect_norm 0.8892，lrr 0.9318，log_rank 0.0675，entropy 0.1315，top10 0.914，lp_burstiness 0.5708，style_cv 0.4562，style_phrases 0.4965，logit_classifier_mpu 0.7397
- **国产新模型 AI 文言故事（DeepSeek / Kimi / 文心一言；不参与校准，但其中 2/3 用于训练文言分类器，没见过的那 1/3 见分类器训练报告）**（50 AI / 0 人写）：AUROC None；检出率 96.0%
  - 各来源检出率：repo-ai-deepseek 96%，repo-ai-kimi 92%，repo-ai-wenxin 100%
  - 各特征 AUROC：

## 诗词
- 数据：ChangAn 当代旧体诗词（人写）+ DeepSeek / 豆包 / GPT-4.1 生成诗词；诗词专用分类器（ChangAn 训练集微调）
- 校准集 400 人写 / 399 AI；阈值 0.8621；交叉验证 AUROC 0.9702；特征 logit_classifier
- 特征组合比较（四种独立对照的 AUROC，按最差情况选）：
  - 只用诗词分类器：ChangAn 保留集 0.955，故事诗 vs 当代人写 0.6772，ChangAn AI vs 唐宋名篇 0.9707，故事诗 vs 唐宋名篇 0.7362，国产新模型诗 vs 当代人写 0.9458，国产新模型诗 vs 唐宋名篇 0.963（最差 0.6772）
  - 诗词分类器 + 语言模型：ChangAn 保留集 0.9484，故事诗 vs 当代人写 0.7176，ChangAn AI vs 唐宋名篇 0.8459，故事诗 vs 唐宋名篇 0.4507，国产新模型诗 vs 当代人写 0.9642，国产新模型诗 vs 唐宋名篇 0.8708（最差 0.4507）
  - 诗词分类器 + 通用分类器 + 语言模型：ChangAn 保留集 0.9496，故事诗 vs 当代人写 0.683，ChangAn AI vs 唐宋名篇 0.8064，故事诗 vs 唐宋名篇 0.4012，国产新模型诗 vs 当代人写 0.9474，国产新模型诗 vs 唐宋名篇 0.7964（最差 0.4012）
  - 通用分类器 + 语言模型：ChangAn 保留集 0.8769，故事诗 vs 当代人写 0.5923，ChangAn AI vs 唐宋名篇 0.3359，故事诗 vs 唐宋名篇 0.1325，国产新模型诗 vs 当代人写 0.8361，国产新模型诗 vs 唐宋名篇 0.3113（最差 0.1325）
  - 只用语言模型：ChangAn 保留集 0.8379，故事诗 vs 当代人写 0.6759，ChangAn AI vs 唐宋名篇 0.2267，故事诗 vs 唐宋名篇 0.1128，国产新模型诗 vs 当代人写 0.9366，国产新模型诗 vs 唐宋名篇 0.3101（最差 0.1128）
- 选用：只用诗词分类器
- **国产新模型 AI 诗词（DeepSeek / Kimi / 文心一言，用户提供，不参与校准）**（70 AI / 0 人写）：AUROC None；检出率 78.6%
  - 各来源检出率：repo-ai-deepseek 83%，repo-ai-kimi 65%，repo-ai-wenxin 87%
  - 各特征 AUROC：
- **ChangAn 保留集（另一批作者 + 没见过的 Kimi-K2 与其他模型的新诗词）**（300 AI / 300 人写）：AUROC 0.955；检出率 74.7%；误判率 3.7%
  - 各来源检出率：Deepseek 71%，gpt-4.1 87%，kimi-k2 67%，seed 75%
  - 各来源误判率：human 4%
  - 各特征 AUROC：fastdetect 0.7595，binoculars 0.2502，logit_classifier 0.955，fastdetect_norm 0.7519，lrr 0.8175，log_rank 0.1699，entropy 0.2499，top10 0.7724，lp_burstiness 0.5953，style_cv 0.4707，style_phrases 0.5，logit_classifier_mpu 0.7237
- **复述故事情节的 AI 诗词（本仓库自带，40 首）**（40 AI / 0 人写）：AUROC None；检出率 5.0%
  - 各来源检出率：repo-ai-story-poem 5%
  - 各特征 AUROC：
- **唐诗三百首 + 宋词三百首（人写名篇，检查误判）**（0 AI / 646 人写）：AUROC None；误判率 1.7%
  - 各来源误判率：唐诗三百首 2%，宋词三百首 1%
  - 各特征 AUROC：
