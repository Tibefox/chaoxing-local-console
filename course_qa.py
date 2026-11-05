"""Local Chinese BM25 retrieval and bounded course question answering."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import time
import sys
from local_model import generate, start

ROOT=Path(__file__).resolve().parent
def tokens(text):
    out=[]
    for term in re.findall(r'[a-z0-9_]+|[\u3400-\u9fff]+',text.lower()):
        if re.match('[a-z0-9_]',term):out.append(term)
        else:out.extend(term[i:i+2] for i in range(len(term)-1))
    return out

class Knowledge:
    def __init__(self,path=None):
        self.chunks=[]
        for line in (path or ROOT/'materials/slides.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line)
            parts=[]
            for key,label in [('text','正文'),('notes','讲者备注'),('chart_text','图表文字'),('alt_text','替代文字'),('ocr_supplement','OCR补充，可信度低于正文')]:
                value='\n'.join(line for line in r.get(key,'').splitlines()
                                if not re.fullmatch(r'\d+|软件工程[-－—].*2024.*',line.strip()))
                if value:parts.append('['+label+']\n'+value)
            text='\n'.join(parts).strip()
            for start in range(0,len(text),850):
                self.chunks.append({'source':f"{r['file']} 第{r['slide']}页",'text':text[start:start+1000]})
        self.counts=[Counter(tokens(c['text'])) for c in self.chunks]
        df=Counter(t for c in self.counts for t in c)
        self.idf={t:math.log(1+(len(self.chunks)-n+.5)/(n+.5)) for t,n in df.items()}
        self.lengths=[sum(c.values()) for c in self.counts]
        self.avg=sum(self.lengths)/max(1,len(self.lengths))

    def search(self,question,limit=5,max_chars=4200):
        q=set(tokens(question));scores=[]
        for i,c in enumerate(self.counts):
            score=sum(self.idf.get(t,0)*c[t]*2.2/(c[t]+1.2*(.25+.75*self.lengths[i]/self.avg)) for t in q if c[t])
            if score>0:scores.append((score,i))
        selected=[];used=set();length=0
        for score,i in sorted(scores,reverse=True):
            c=self.chunks[i]
            if c['source'] in used:continue
            content=c['text'][:max_chars-length]
            if not content:break
            selected.append({**c,'text':content,'score':round(score,2)});used.add(c['source']);length+=len(content)
            if len(selected)>=limit:break
        return selected

    def answer(self,question):
        begin=time.perf_counter();hits=self.search(question)
        retrieval=time.perf_counter()-begin
        context='\n\n'.join('【'+h['source']+'】\n'+h['text'] for h in hits)
        result=generate(question,context)
        return {**result,'retrieval_ms':round(retrieval*1000,2),'total_seconds':round(time.perf_counter()-begin,3),
                'sources':[h['source'] for h in hits],'context_characters':len(context)}

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('question');p.add_argument('--search-only',action='store_true');a=p.parse_args()
    if not a.search_only:start()
    k=Knowledge();print(json.dumps(k.search(a.question) if a.search_only else k.answer(a.question),ensure_ascii=False,indent=2))
