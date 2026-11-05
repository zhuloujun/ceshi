# 检测效果评估报告

每种文体各自校准：先识别段落是现代汉语、文言还是英文，再用对应的分类器和阈值判断。
语言模型：Qwen/Qwen2.5-1.5B + Qwen/Qwen2.5-1.5B-Instruct；中文分类器 yuchuantian/AIGC_detector_zhv3；英文分类器 desklib/ai-text-detector-v1.01。

“检出率”= AI 文本被判为 AI 的比例；“误判率”= 人写文本被误判为 AI 的比例；AUROC 1 为完美区分，0.5 为随机。

## 现代汉语
- 数据：NLPCC 2025 Task 1（CSL 学术摘要 / 新闻 / 作文；GPT-4o、GLM-4、Qwen）
- 校准集 400 人写 / 396 AI；阈值 0.5；交叉验证 AUROC 0.9998；特征 fastdetect, binoculars, logit_classifier, fastdetect_norm, lrr, log_rank, entropy, top10, lp_burstiness, style_cv, style_phrases
- 特征组合比较（校准集交叉验证 AUROC）：全部特征 0.9998，三个主信号 0.9999，语言模型特征 0.9783，全部特征 + 惊奇度多样性（DivEye） 0.9998 → 选用全部特征
- **NLPCC 测试集（训练时未见，含 DeepSeek-V3）**（200 AI / 200 人写）：AUROC 0.9738；检出率 84.5%；误判率 3.5%
  - 各特征 AUROC：fastdetect 0.846，binoculars 0.153，logit_classifier 0.9728，fastdetect_norm 0.8456，lrr 0.8473，log_rank 0.1726，entropy 0.1837，top10 0.8673，lp_burstiness 0.8845，style_cv 0.2175，style_phrases 0.5068
- **CSL 学术摘要保留集**（180 AI / 60 人写）：AUROC 1.0；检出率 100.0%；误判率 0.0%
  - 各来源检出率：glm 100%，gpt4o 100%，qwen 100%
  - 各来源误判率：human 0%
  - 各特征 AUROC：fastdetect 0.9587，binoculars 0.0421，logit_classifier 0.9997，fastdetect_norm 0.9558，lrr 0.9843，log_rank 0.0313，entropy 0.1302，top10 0.9761，lp_burstiness 0.6775，style_cv 0.2154，style_phrases 0.8452

## 现代汉语短段
- 数据：NLPCC 2025 Task 1 样本截成 80–260 字的短段
- 校准集 362 人写 / 369 AI；阈值 0.7616；交叉验证 AUROC 0.9967；特征 fastdetect, binoculars, logit_classifier, fastdetect_norm, lrr, log_rank, entropy, top10, lp_burstiness, style_cv, style_phrases
- 特征组合比较（校准集交叉验证 AUROC）：全部特征 0.9967，三个主信号 0.9955，语言模型特征 0.9368，全部特征 + 惊奇度多样性（DivEye） 0.997 → 选用全部特征
- **NLPCC 测试集截成 80–260 字的短段（含本仓库 AI 读后感 / 散文）**（207 AI / 186 人写）：AUROC 0.9307；检出率 77.3%；误判率 3.8%
  - 各来源检出率：repo-ai-zh-essay 96%
  - 各特征 AUROC：fastdetect 0.8207，binoculars 0.1767，logit_classifier 0.9268，fastdetect_norm 0.8215，lrr 0.734，log_rank 0.2508，entropy 0.3112，top10 0.7798，lp_burstiness 0.7272，style_cv 0.344，style_phrases 0.5053

## 英文
- 数据：MAGE（人写文本与 GPT-3.5 / GPT-4 等生成文本）
- 校准集 296 人写 / 300 AI；阈值 0.704；交叉验证 AUROC 0.9674；特征 fastdetect, binoculars, logit_classifier
- **MAGE：GPT-4 在未见过的领域生成的文本**（150 AI / 150 人写）：AUROC 0.9889；检出率 93.3%；误判率 5.3%
  - 各来源检出率：cnn_gpt4 91%，imdb_gpt4 96%，pubmed_gpt4 89%，dialogsum_gpt4 97%
  - 各来源误判率：pubmed_human 6%，dialogsum_human 15%，imdb_human 0%，cnn_human 0%
  - 各特征 AUROC：fastdetect 0.7674，binoculars 0.2388，logit_classifier 0.9861，fastdetect_norm 0.7653，lrr 0.7717，log_rank 0.1626，entropy 0.2311，top10 0.8336，lp_burstiness 0.7177，style_cv 0.3014，style_phrases 0.6956
- **MAGE：GPT-4 文本经改写后（含本仓库英文 AI 样本）**（173 AI / 150 人写）：AUROC 0.8913；检出率 68.8%；误判率 12.0%
  - 各来源检出率：pubmed_gpt4_para 80%，cnn_gpt4_para 58%，imdb_gpt4_para 71%，dialogsum_gpt4_para 74%，repo-ai-english 57%
  - 各来源误判率：pubmed_human 11%，imdb_human 0%，pubmed_human_para 0%，cnn_human 6%，dialogsum_human_para 39%，cnn_human_para 14%，imdb_human_para 18%，dialogsum_human 0%
  - 各特征 AUROC：fastdetect 0.5425，binoculars 0.4629，logit_classifier 0.8943，fastdetect_norm 0.5399，lrr 0.6221，log_rank 0.3504，entropy 0.3618，top10 0.6514，lp_burstiness 0.5103，style_cv 0.3128，style_phrases 0.5914

## 文言
- 数据：NiuTrans 古文语料（人写）+ 大语言模型生成的文言样本
- 校准集 373 人写 / 198 AI；阈值 0.9104；交叉验证 AUROC 0.8966；特征 fastdetect, binoculars, logit_classifier
- **文言保留集（另一组古籍 + 未参与校准的 AI 文言）**（146 AI / 278 人写）：AUROC 0.915；检出率 44.5%；误判率 1.1%
  - 各来源检出率：llm-classical 10%，gen-claude 0%，gen-deepseek 79%，gen-doubao 50%，gen-kimi 100%，gen-qwen 78%
  - 各特征 AUROC：fastdetect 0.8332，binoculars 0.183，logit_classifier 0.8756，fastdetect_norm 0.8252，lrr 0.8154，log_rank 0.1993，entropy 0.2751，top10 0.7902，lp_burstiness 0.5257，style_cv 0.4362，style_phrases 0.5016

## 诗词
- 数据：ChangAn 当代旧体诗词（人写）+ DeepSeek / 豆包 / GPT-4.1 生成诗词；诗词专用分类器（ChangAn 训练集微调）
- 校准集 400 人写 / 399 AI；阈值 0.8858；交叉验证 AUROC 0.9735；特征 logit_classifier
- 特征组合比较（四种独立对照的 AUROC，按最差情况选）：
  - 只用诗词分类器：ChangAn 保留集 0.9532，故事诗 vs 当代人写 0.6431，ChangAn AI vs 唐宋名篇 0.9566，故事诗 vs 唐宋名篇 0.6486（最差 0.6431）
  - 诗词分类器 + 语言模型：ChangAn 保留集 0.9596，故事诗 vs 当代人写 0.7318，ChangAn AI vs 唐宋名篇 0.4734，故事诗 vs 唐宋名篇 0.0683（最差 0.0683）
  - 诗词分类器 + 通用分类器 + 语言模型：ChangAn 保留集 0.9581，故事诗 vs 当代人写 0.6825，ChangAn AI vs 唐宋名篇 0.4526，故事诗 vs 唐宋名篇 0.0749（最差 0.0749）
  - 通用分类器 + 语言模型：ChangAn 保留集 0.9015，故事诗 vs 当代人写 0.6828，ChangAn AI vs 唐宋名篇 0.0735，故事诗 vs 唐宋名篇 0.019（最差 0.019）
  - 只用语言模型：ChangAn 保留集 0.8665，故事诗 vs 当代人写 0.7508，ChangAn AI vs 唐宋名篇 0.0315，故事诗 vs 唐宋名篇 0.0109（最差 0.0109）
- 选用：只用诗词分类器
- **ChangAn 保留集（另一批作者 + 没见过的 Kimi-K2 与其他模型的新诗词）**（300 AI / 300 人写）：AUROC 0.9532；检出率 71.0%；误判率 2.0%
  - 各来源检出率：Deepseek 57%，gpt-4.1 91%，kimi-k2 61%，seed 75%
  - 各来源误判率：human 2%
  - 各特征 AUROC：fastdetect 0.7767，binoculars 0.2338，logit_classifier 0.9532，fastdetect_norm 0.7742，lrr 0.8432，log_rank 0.1452，entropy 0.2052，top10 0.7993，lp_burstiness 0.5925，style_cv 0.4707，style_phrases 0.5，logit_classifier_mpu 0.7237
- **复述故事情节的 AI 诗词（本仓库自带，40 首）**（40 AI / 0 人写）：AUROC None；检出率 0.0%
  - 各来源检出率：repo-ai-story-poem 0%
  - 各特征 AUROC：
- **唐诗三百首 + 宋词三百首（人写名篇，检查误判）**（0 AI / 646 人写）：AUROC None；误判率 2.5%
  - 各来源误判率：唐诗三百首 4%，宋词三百首 1%
  - 各特征 AUROC：
