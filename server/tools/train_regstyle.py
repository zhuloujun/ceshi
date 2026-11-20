"""训练 app/data/regstyle.json.xz（句子文体：古典 vs 现代白话）。数据路径为本地解密后的用户语料，不进仓库。"""
import sys,json,re,random,collections
import numpy as np
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.linear_model import LogisticRegression
random.seed(0)
CJ=re.compile(r'[一-鿿]')
def sent(s): return [x for x in re.split(r'(?<=[。！？；])',s) if len(CJ.findall(x))>=8]
U=[json.loads(l) for l in open('/tmp/claude-0/w20/ud/u27.jsonl')]
pos=[];neg=[]
for r in U:
    if r['r'] in ('c','p','cp') and random.random()<0.35: pos+=sent(r['t'])[:4]
    elif r['r']=='z' and random.random()<0.25: neg+=sent(r['t'])[:4]
A=json.load(open('/tmp/claude-0/bk1120/rows_ai1120.json'))
SQ=('a1120-a681265d','a1120-c076531b')
for r in A:
    if r['r'] in ('c','p') or r['d'] in SQ: pos+=sent(r['t'])
# 元曲 originals (human) from chinese-poetry, if present
import glob
for f in glob.glob('/tmp/claude-0/**/yuanqu*.json',recursive=True)[:1]:
    for it in json.load(open(f)): pos+=sent(''.join(it.get('paragraphs',[])))
print('pos',len(pos),'neg',len(neg))
random.shuffle(pos); random.shuffle(neg); n=min(len(pos),len(neg),40000); pos=pos[:n]; neg=neg[:n]
X=pos+neg; y=[1]*len(pos)+[0]*len(neg)
idx=list(range(len(X))); random.shuffle(idx); cut=int(.9*len(idx))
tr=[X[i] for i in idx[:cut]]; ytr=[y[i] for i in idx[:cut]]; te=[X[i] for i in idx[cut:]]; yte=[y[i] for i in idx[cut:]]
prep=lambda s:''.join(c if CJ.match(c) or c in '，、；' else ' ' for c in s)
cv=CountVectorizer(analyzer='char',ngram_range=(1,2),min_df=5,max_features=40000,preprocessor=prep,binary=True)
Xtr=cv.fit_transform(tr); clf=LogisticRegression(C=0.5,max_iter=2000).fit(Xtr,ytr)
print('test acc',clf.score(cv.transform(te),yte))
voc=cv.vocabulary_; w=clf.coef_[0]
W={k:round(float(w[i]),3) for k,i in voc.items() if abs(w[i])>0.05}
json.dump({'b':float(clf.intercept_[0]),'w':W},open('/tmp/claude-0/w20s/regw.json','w'),ensure_ascii=False)
print('feats',len(W))
