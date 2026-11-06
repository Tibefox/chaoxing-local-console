"""Discover recent quiz cards across adjacent screens and dispatch one group card at a time."""
import datetime as dt
import json
import time

import group_watch as g

MAX_PAGES = 8
_retry_icons = {}


def setup(db):
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS group_queue "
                   "(card_key TEXT UNIQUE, posted REAL, retry_after REAL DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS group_card_kinds(card_key TEXT PRIMARY KEY,kind TEXT)")
        # Preserve pending cards from the previous desktop version.
        db.execute("INSERT OR IGNORE INTO group_queue(card_key,posted) "
                   "SELECT card_key,observed FROM group_cards WHERE state='练习待处理' ORDER BY observed,rowid")
        # Only retry interruption before a submit intent was recorded.
        db.execute("UPDATE group_cards SET state='练习待处理' WHERE state='正在打开练习' "
                   "AND card_key IN (SELECT card_key FROM group_queue) AND NOT EXISTS "
                   "(SELECT 1 FROM group_submission_sources s WHERE s.receipt=group_cards.card_key)")
        db.execute("UPDATE group_cards SET state='提交待核对' WHERE state='正在打开练习' "
                   "AND card_key IN (SELECT receipt FROM group_submission_sources)")


def message_time(detail, now):
    for value in detail:
        for year in (now.year, now.year-1):
            try:
                stamp=dt.datetime.strptime(f"{year}-{value}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if stamp <= now+dt.timedelta(minutes=1):
                return stamp.timestamp()
    return None


def page(serial,db,root,cutoff,baseline,cache):
    now=dt.datetime.now()
    result=[]
    frame=None
    for key,title,node,detail in g.card_rows(root):
        if key not in cache:
            stamp=message_time(detail,now)
            known=db.execute("SELECT state,observed FROM group_cards WHERE card_key=?",(key,)).fetchone()
            eligible=False
            # A transient configuration/icon failure must not age a newly observed
            # activity out of the moving scan cutoff before it can be verified.
            pending_recent=(known and known[0]=="group_card" and known[1]>=time.time()-7200
                            and stamp is not None and stamp>=known[1]-120)
            if (not baseline and (not known or known[0]=="group_card") and stamp is not None and (stamp>=cutoff or pending_recent)
                    and _retry_icons.get(key,0)<=time.monotonic()):
                if frame is None:
                    frame=g.shell(serial,"exec-out","screencap")
                eligible=g.quiz_card(serial,node,detail,True,frame)
                if not eligible and g.vote_enabled():
                    eligible='vote' if g.vote_card(serial,node,frame) else False
                if not eligible:
                    _retry_icons[key]=time.monotonic()+5
            cache[key]=(key,title,stamp,eligible)
        result.append(cache[key])
    return result


def scroll(serial,root,direction,stop):
    history=g.nodes(root,"lv_chat_his")
    if stop.is_set() or len(history)!=1 or history[0].get(direction)!="true":
        return None
    if g.snapshot(serial,scroll=direction).get("scrolled")!="true":
        return None
    if stop.wait(.35):
        return None
    return g.snapshot(serial)


def register(db,pages,baseline):
    with db:
        for visible in pages:  # oldest scanned page first, then group display order
            for key,title,stamp,eligible in visible:
                state="group_baseline" if baseline else "练习待处理" if eligible else "group_card"
                inserted=db.execute("INSERT OR IGNORE INTO group_cards VALUES(?,?,?,?,?)",
                                    (key,g.GROUP,time.time(),title,state)).rowcount
                if eligible:
                    db.execute("INSERT OR IGNORE INTO group_card_kinds VALUES(?,?)",
                               (key,'vote' if eligible=='vote' else 'quiz'))
                    if not inserted:
                        db.execute("UPDATE group_cards SET state='练习待处理' WHERE card_key=? AND state='group_card'",(key,))
                    db.execute("INSERT OR IGNORE INTO group_queue(card_key,posted) VALUES(?,?)",(key,stamp))


def next_job(db):
    with db:
        db.execute("UPDATE group_cards SET state='过期·需核对' WHERE state='练习待处理' AND observed<? AND group_name=?",(time.time()-7200,g.GROUP))
    return db.execute("SELECT q.card_key,q.posted FROM group_queue q JOIN group_cards c ON c.card_key=q.card_key "
                      "WHERE c.state='练习待处理' AND q.retry_after<=? AND c.group_name=? ORDER BY q.posted,q.rowid LIMIT 1",
                      (time.time(),g.GROUP)).fetchone()


def scan(serial,db,root,baseline,stop,report,auto):
    for key,until in list(_retry_icons.items()):
        if until<=time.monotonic(): _retry_icons.pop(key,None)
    row=db.execute("SELECT value FROM group_watch_meta WHERE name=?",(g.meta_key('queue_tail'),)).fetchone()
    previous=json.loads(row[0]) if row else {}
    cutoff=max(time.time()-7200,min(time.time()-120,previous.get("time",time.time())-65))
    cache={}
    def collect(tree):
        return page(serial,db,tree,cutoff,baseline or not auto,cache)
    pages=[collect(root)]
    # If the list retained an older position, follow newer messages before
    # deciding which activity is first. Capture every page we pass.
    for _ in range(MAX_PAGES):
        new=scroll(serial,root,"newer",stop)
        if new is None: break
        if not g.target_chat(new):
            register(db,pages,baseline)
            return None,None
        root=new
        pages.append(collect(root))
    newest=[r[0] for r in pages[-1]]
    anchors=set(previous.get("keys",[]))
    def seen_anchor():
        return any(record[0] in anchors for visible in pages for record in visible)
    if not baseline and anchors and not seen_anchor():
        for _ in range(MAX_PAGES):
            older=scroll(serial,root,"older",stop)
            if older is None: break
            if not g.target_chat(older):
                register(db,pages,baseline)
                return None,None
            root=older
            pages.insert(0,collect(root))
            if seen_anchor() or any(r[2] and r[2]<cutoff for r in pages[0]):
                break
    register(db,pages,baseline)
    with db:
        if newest:
            db.execute("INSERT OR REPLACE INTO group_watch_meta VALUES(?,?)",
                       (g.meta_key('queue_tail'),json.dumps({"keys":newest,"time":time.time()})))
    job=next_job(db) if auto and not baseline else None
    if not job: return None,None
    key,posted=job
    def retain(new):
        register(db,[collect(new)],False)
    node=locate(serial,root,key,posted,stop,report,retain)
    if node is not None:
        return node,key
    # Don't discard an offscreen card, and don't let it block other visible jobs.
    with db:
        db.execute("UPDATE group_queue SET retry_after=? WHERE card_key=?",(time.time()+20,key))
    return None,None


def locate(serial,root,key,posted,stop,report,retain=lambda root:None):
    visit=set()
    for _ in range(MAX_PAGES+1):
        rows=g.card_rows(root)
        if rows is None or stop.is_set(): return None
        for k,_,node,_ in rows:
            if k==key:
                return node
        signature=tuple(k for k,_,_,_ in rows)
        times=[message_time(detail,dt.datetime.now()) for _,_,_,detail in rows]
        direction="newer" if any(t and t<posted for t in times) else "older"
        history=g.nodes(root,"lv_chat_his")[0]
        if (signature,direction) in visit or history.get(direction)!="true":
            direction="older" if direction=="newer" else "newer"
        if (signature,direction) in visit: break
        visit.add((signature,direction))
        report("正在群记录中定位队首练习")
        new=scroll(serial,root,direction,stop)
        if new is None: break
        if not g.target_chat(new): return None
        root=new
        retain(root)
    return None
