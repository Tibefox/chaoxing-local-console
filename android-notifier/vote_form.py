"""Pure parser for the controls, title and limits of a single vote."""
import re
from question_capture import APP, text


def label(node):
    values=[]
    for n in node.iter('node'):
        t=text(n)
        if t and t not in values:
            values.append(t)
    return ' '.join(values)


def parse_form(root):
    webs = [n for n in root.iter('node') if n.get('package') == APP and
            n.get('class') == 'android.webkit.WebView' and not any(
                child is not n and child.get('class')=='android.webkit.WebView' for child in n.iter('node'))]
    if len(webs) != 1:
        raise ValueError('投票正文未就绪')
    web = webs[0]
    all_nodes=list(web.iter('node'))
    controls=[n for n in all_nodes if n.get('class') in
              {'android.widget.RadioButton','android.widget.CheckBox'}]
    description=' '.join(text(n) for n in all_nodes)
    multiple=bool(re.search(r'多选|至少.{0,3}\d+.{0,2}[项个]|最多.{0,3}[2-9]\d*.{0,2}[项个]',description))
    explicit_single='单选' in description
    if controls:
        classes={n.get('class') for n in controls}
        if len(classes)!=1:
            raise ValueError('混合控件或多题投票需核对')
        multiple=classes=={'android.widget.CheckBox'}
        options=[(label(n),n) for n in controls]
        first=min(all_nodes.index(n) for n in controls)
    else:
        main=[n for n in all_nodes if n.get('resource-id')=='main']
        if len(main)!=1:
            raise ValueError('投票选项容器无法核验')
        controls=[n for n in main[0].iter('node') if n.get('clickable')=='true' and label(n)
                  and not any(c is not n and c.get('clickable')=='true' and label(c) for c in n.iter('node'))]
        options=[(label(n),n) for n in controls]
        # Real quick-vote forms share main/item and text-only clickable choices:
        # attitude, true/false, two letters, or four letters. Labels are data.
        items=[n for n in main[0].iter('node') if n.get('resource-id')=='item']
        quick=(len(items)==1 and len(controls) in (2,4) and
               all(n.get('class')=='android.widget.TextView' and n in list(items[0]) for n in controls))
        if not (multiple or explicit_single or quick):
            raise ValueError('页面未说明单选或多选规则')
        first=all_nodes.index(main[0])
    if not 2<=len(options)<=20 or any(not t for t,n in options) or len({t for t,n in options})!=len(options):
        raise ValueError('投票选项为空、重复或数量超限')
    if any(n.get('class')=='android.widget.Image' for n in all_nodes[first:]):
        raise ValueError('图片投票需要图像内容适配')
    metadata=re.compile(r'^(?:投票|单选|多选|剩余.*|(?:至少|最多|限选|可选|请选择).*|\d{1,2}-\d{1,2}\s.*)$')
    titles=[text(n) for n in all_nodes[1:first] if text(n) and
            n.get('class')=='android.widget.TextView' and not metadata.fullmatch(text(n))]
    title=' '.join(dict.fromkeys(titles)).strip()
    if not title:
        raise ValueError('投票题目无法唯一核验')
    lower,upper=1,len(options) if multiple else 1
    if multiple:
        lo=re.search(r'至少\s*(\d+)\s*[项个]',description)
        hi=re.search(r'最多\s*(\d+)\s*[项个]',description)
        exact=re.search(r'(?:限选|请选择)\s*(\d+)\s*[项个]',description)
        if lo: lower=int(lo[1])
        if hi: upper=int(hi[1])
        if exact: lower=upper=int(exact[1])
    if not 1<=lower<=upper<=len(options):
        raise ValueError('投票选择数量规则冲突')
    keys='ABCDEFGHIJKLMNOPQRST'
    return (title,{k:t for k,(t,n) in zip(keys,options)},
                        {k:n for k,(t,n) in zip(keys,options)},
                        'multiple' if multiple else 'single',lower,upper)
