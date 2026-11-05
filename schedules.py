"""Beijing weekly time validation; execution is owned by android-notifier/weekly_control.py."""
import datetime as dt
import hashlib
import re
TZ=dt.timezone(dt.timedelta(hours=8))

def parse_time(value):
    parsed=dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)

def validate(rows):
    clean=[]
    for row in rows:
        start,end=parse_time(row['start']),parse_time(row['end'])
        if end<=start:raise ValueError('结束时间必须晚于开始时间')
        if (end-start).total_seconds()>24*3600:raise ValueError('每段最长24小时')
        ident=hashlib.sha256((start.isoformat()+end.isoformat()).encode()).hexdigest()[:16]
        clean.append({'id':ident,'start':start.isoformat(),'end':end.isoformat()})
    clean.sort(key=lambda r:r['start'])
    for a,b in zip(clean,clean[1:]):
        if parse_time(a['end'])>parse_time(b['start']):raise ValueError('运行时段不能重叠')
    return clean

def validate_weekly(rows):
    out=[]
    for r in rows:
        if not all(re.fullmatch(r'\d{2}:\d{2}',r.get(k,'')) for k in ('start','end')):raise ValueError('每周时间格式必须为HH:MM')
        days=sorted(set(int(d) for d in r['days']))
        if not days or any(d<0 or d>6 for d in days):raise ValueError('星期需在周一至周日之间')
        a=dt.time.fromisoformat(r['start']);b=dt.time.fromisoformat(r['end'])
        if a==b:raise ValueError('开始与结束时间不能相同')
        a=a.strftime('%H:%M');b=b.strftime('%H:%M')
        ident=hashlib.sha256((str(days)+a+b).encode()).hexdigest()[:16]
        out.append({'id':ident,'days':days,'start':a,'end':b})
    # Expand a representative fortnight to catch overnight/week-boundary overlaps.
    expanded=[];base=dt.date(2026,11,2)
    for offset in range(15):
        day=base+dt.timedelta(days=offset)
        for r in out:
            if day.weekday() in r['days']:
                a=parse_time(str(day)+'T'+r['start']);b=parse_time(str(day)+'T'+r['end'])
                if b<=a:b+=dt.timedelta(days=1)
                expanded.append({'start':a.isoformat(),'end':b.isoformat()})
    validate(expanded)
    return out
