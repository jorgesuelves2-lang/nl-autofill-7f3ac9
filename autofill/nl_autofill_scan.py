#!/usr/bin/env python3
"""Detecta leads de NatschoLibre a rellenar (SETTING + TRIAJE). NO escribe.
Vuelca /tmp/nl_autofill_pending.json con el contexto (campos + notas + transcripcion Fathom).

Secretos: variables de entorno (CI) o ~/.natscholibre_secrets/*.env (local).
MODOS: (def) ETIQUETA 'triaje-listo' | --backlog (calendario, para vaciar pendientes).
Optimizacion: en modo etiqueta, si no hay nadie marcado, sale al instante sin bajar Fathom.
"""
import subprocess, os, re, json, html, unicodedata, sys, time
from concurrent.futures import ThreadPoolExecutor
def env(k, f="ghl.env"):
    v=os.environ.get(k)
    if v: return v
    p=os.path.expanduser(f"~/.natscholibre_secrets/{f}")
    if os.path.exists(p):
        m=re.search(rf'{k}=(.+)',open(p).read())
        if m: return m.group(1).strip()
    raise SystemExit(f"falta secreto {k}")
T=env("GHL_TOKEN"); LOC=env("GHL_LOCATION_ID"); FKEY=env("FATHOM_API_KEY","fathom.env")
H=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-07-28","-H","Accept: application/json"]
HP=H+["-H","Content-Type: application/json"]
TRIAGE_CAL="2EY5mRYqpaAx4qfnsWJM"; DAYS=30
READY_TAG="triaje-listo"; DONE_TAG="claude-analizado"
F_ANALISIS_SETTING="bhgSTSIi5k9tCfiDQFD5"; F_ANALISIS_TRIAJE="tXb9dblrmzhtTZqdmBBj"
BACKLOG="--backlog" in sys.argv
OUT="/tmp/nl_autofill_pending.json"
def cg(u,key=None):
    hdr=["-H",f"X-Api-Key: {key}"] if key else H
    for _ in range(4):
        r=subprocess.run(["curl","-s","-m","30",u,*hdr],capture_output=True,text=True).stdout
        if r:
            try: return json.loads(r)
            except: pass
        time.sleep(0.5)
    return {}
def csearch(body):
    for _ in range(4):
        r=subprocess.run(["curl","-s","-m","30","-X","POST","https://services.leadconnectorhq.com/contacts/search",*HP,"--data",json.dumps(body)],capture_output=True,text=True).stdout
        if r:
            try: return json.loads(r)
            except: pass
    return {}
def strip(s):
    s=re.sub(r'<br\s*/?>','\n',s or ''); s=re.sub(r'</(p|li|ul|div|tr)>','\n',s); s=re.sub(r'<[^>]+>','',s); return html.unescape(s).strip()
def norm(s):
    s=unicodedata.normalize('NFKD',(s or '').lower()); s=''.join(c for c in s if not unicodedata.combining(c))
    s=re.sub(r'\b(ing|dr|dra|md|mg|med|odont|e-md|arg)\b','',s); return re.sub(r'[^a-z ]','',s).split()
def nkey(s): return " ".join(norm(s)[:2])

# 1) descubrir leads
if BACKLOG:
    import datetime
    now=int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000); cut=now-DAYS*86400*1000
    ev=cg(f"https://services.leadconnectorhq.com/calendars/events?locationId={LOC}&calendarId={TRIAGE_CAL}&startTime={cut}&endTime={now}").get("events",[])
    cids=[]; seen=set()
    for e in ev:
        c=e.get("contactId")
        if c and c not in seen: seen.add(c); cids.append(c)
else:
    page=1; cids=[]
    while True:
        d=csearch({"locationId":LOC,"page":page,"pageLimit":100,"filters":[{"field":"tags","operator":"eq","value":READY_TAG}]})
        cs=d.get("contacts",[])
        cids+=[c["id"] for c in cs if DONE_TAG not in (c.get("tags") or [])]
        if len(cs)<100: break
        page+=1

if not cids:
    json.dump([],open(OUT,"w"))
    print(f"Modo: {'BACKLOG' if BACKLOG else 'ETIQUETA'} | sin pendientes."); raise SystemExit(0)

# 2) Fathom: triajes con transcripcion -> mapa por nombre (solo si hay candidatos)
fmap={}; cur=None
for _ in range(16):
    u='https://api.fathom.ai/external/v1/meetings?include_transcript=true&limit=25'+(f'&cursor={cur}' if cur else '')
    d=cg(u,key=FKEY)
    for m in d.get("items",[]):
        title=m.get("title") or ""
        if not re.search(r'triage|triaje',title,re.I): continue
        lead=re.split(r'\s*-\s*',title)[0]
        tr=m.get("transcript") or []
        txt="\n".join(f"{(t.get('speaker') or {}).get('display_name','?')}: {t.get('text','')}" for t in tr)
        if txt: fmap[nkey(lead)]={"transcript":txt[:16000]}
    cur=d.get("next_cursor")
    if not cur: break
cat={f["id"]:f.get("name") for f in cg(f"https://services.leadconnectorhq.com/locations/{LOC}/customFields").get("customFields",[])}

def fetch(cid):
    c=cg(f"https://services.leadconnectorhq.com/contacts/{cid}").get("contact",{})
    notes=cg(f"https://services.leadconnectorhq.com/contacts/{cid}/notes").get("notes",[])
    cf={x.get("id"):x.get("value") for x in c.get("customFields",[])}
    tags=c.get("tags",[]) or []
    if DONE_TAG in tags: return None
    notes_txt=[strip(n.get("body")) for n in notes]
    nombre=c.get("contactName") or ((c.get("firstName") or "")+" "+(c.get("lastName") or "")).strip()
    fa=fmap.get(nkey(nombre))
    has_note=any(("contexto del prospecto" in n.lower() or "fathom.video/share" in n.lower()) for n in notes_txt)
    needs_setting=not (cf.get(F_ANALISIS_SETTING) or "").strip()
    needs_triage=(bool(fa) or has_note or any(t.startswith("triage-") for t in tags)) and not (cf.get(F_ANALISIS_TRIAJE) or "").strip()
    if not (needs_setting or needs_triage): return None
    filled={cat.get(k,k):v for k,v in cf.items() if v not in (None,"") and not str(k).startswith("Analisis")}
    return {"contact_id":cid,"nombre":nombre,"tags":tags,
            "needs_setting":needs_setting,"needs_triage":needs_triage,
            "campos_formulario":filled,"notas":notes_txt,
            "transcripcion_triaje": fa["transcript"] if fa else None}
out=[]
with ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(fetch,cids):
        if r: out.append(r)
json.dump(out,open(OUT,"w"),ensure_ascii=False)
print(f"Modo: {'BACKLOG' if BACKLOG else 'ETIQUETA'} | candidatos: {len(cids)} | PENDIENTES: {len(out)}")
for r in out:
    f=[]
    if r["needs_setting"]: f.append("SETTING")
    if r["needs_triage"]: f.append("TRIAJE"+("(transcr)" if r["transcripcion_triaje"] else "(nota)"))
    print(f"  - {r['nombre']} ({r['contact_id']}) -> {'+'.join(f)}")
