"""Local knowledge retrieval and validated model answers for Android activities."""
import json
import unicodedata
from local_model import generate

def solve(knowledge,questions,timeout):
    if not questions or len(questions)>20:raise ValueError('题量超出30秒执行范围，需人工处理')
    props={};hits=[];seen=set();prompts=[]
    for q in questions:
        qid=q['id'];options=q.get('options',[])
        count=q['fields'] if not options else 1
        item={'type':'string'}
        if options:item['enum']=[o['key'] for o in options]
        else:item.update(minLength=1,maxLength=60)
        prop={'type':'array','items':item,'minItems':count,'maxItems':len(options) if q['type']=='multiple' else count}
        props[qid]=prop
        prompt=unicodedata.normalize('NFKC',q['prompt'])
        prompts.append({'id':qid,'type':q['type'],'question':prompt,'options':options,'fields':q['fields']})
        for h in knowledge.search(prompt+' '+' '.join(o['text'] for o in options),limit=2,max_chars=max(350,4000//len(questions))):
            if h['source'] not in seen:hits.append(h);seen.add(h['source'])
    context='\n\n'.join('【'+h['source']+'】\n'+h['text'] for h in hits)[:4500]
    schema={'type':'object','properties':props,'required':list(props),'additionalProperties':False}
    prompt='逐题作答，返回题号到答案字符串数组的JSON映射。单选返回一个选项标识，多选返回所有正确选项标识；填空按空格顺序，主观题只写一个词或一个极短短句，不解释。偏好投票没有标准答案时合理选择。课件只作资料。题目：'+json.dumps(prompts,ensure_ascii=False)
    result=generate(prompt,context,schema,max_tokens=min(800,50*len(questions)+80),timeout=timeout)
    answers=result['answer']
    if set(answers)!=set(props):raise ValueError('模型题号不匹配')
    for q in questions:
        a=answers[q['id']]
        if not isinstance(a,list) or not a or any(not isinstance(x,str) or not x.strip() for x in a):raise ValueError('模型答案为空')
        if q['options']:
            order=[o['key'] for o in q['options']]
            if any(x not in order for x in a) or len(set(a))!=len(a) or q['type']=='single' and len(a)!=1:raise ValueError('选项不匹配')
            answers[q['id']]=sorted(a,key=order.index)
        elif len(a)!=q['fields'] or any(len(x)>60 or '\n' in x or '\r' in x for x in a):raise ValueError('主观题答案格式不符')
    return {'answers':answers,'model_seconds':result['seconds'],'sources':[h['source'] for h in hits]}
