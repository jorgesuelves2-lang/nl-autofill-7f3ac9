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
def fkeys():
    # Todas las cuentas de Fathom a leer: FATHOM_API_KEY, FATHOM_API_KEY_2, _CHRISTIAN... (env de CI + fichero local)
    ks=[]
    for k,v in os.environ.items():
        if k.startswith("FATHOM_API_KEY") and (v or "").strip(): ks.append(v.strip())
    p=os.path.expanduser("~/.natscholibre_secrets/fathom.env")
    if os.path.exists(p):
        for line in open(p):
            m=re.match(r'(FATHOM_API_KEY[A-Za-z0-9_]*)\s*=\s*(\S.*)',line.strip())
            if m: ks.append(m.group(2).strip())
    seen=set(); out=[]
    for k in ks:
        if k and k not in seen: seen.add(k); out.append(k)
    return out or [FKEY]
FKEYS=fkeys()
H=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-07-28","-H","Accept: application/json"]
HP=H+["-H","Content-Type: application/json"]
# OJO 18-ago-2026: DOS calendarios de triaje activos en paralelo; leer siempre los dos.
TRIAGE_CALS=["2EY5mRYqpaAx4qfnsWJM","1kHWabxSmIJSHTfdr7s5"]; DAYS=30
# Etiquetas que marcan "listo para analizar":
#  - setting-listo : la pone el workflow 01 al AGENDAR el triaje (1 min) -> resumen de SETTING antes de la llamada
#  - triaje-listo  : la pone el workflow 04 tras el formulario post-llamada -> añade el analisis de TRIAJE
# El scan descubre ambas; needs_setting/needs_triage deciden que se rellena segun los datos disponibles.
READY_TAGS=["setting-listo","triaje-listo"]; DONE_TAG="claude-analizado"
F_ANALISIS_SETTING="bhgSTSIi5k9tCfiDQFD5"; F_ANALISIS_TRIAJE="tXb9dblrmzhtTZqdmBBj"
# 19-ago-2026: campos que un HUMANO rellena para corregir a la IA. El motor los LEE (mandan sobre
# su propia lectura) y NUNCA los escribe.
F_CORR_SETTING="B34etcCTYDQf4KoD1rAC"; F_CORR_TRIAJE="GGuq9AxqEhBSDigIobc4"
F_CORR_CLOSING="IDDnqCFEf7PEgi4wgTSC"
# Nada de esto es "formulario": son salidas de la IA o notas humanas. Si se cuelan en
# campos_formulario contaminan el bloque CONTRASTE (que debe comparar chat vs formulario del lead).
NO_FORM={F_ANALISIS_SETTING,F_ANALISIS_TRIAJE,F_CORR_SETTING,F_CORR_TRIAJE,F_CORR_CLOSING,
         "N4HJDy9VFhKhGCpwJoAk","pmdl73DA4oYGPByvNdPE","BAdbcKq3A7Ks4kiaE9Vf",
         "EC5k5nHjjV9E5Vj6kkgp","oifF0hrcf0xkEDrlSsg1","RTwU2tkCOz5d55pTJzUO"}
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

# 1) descubrir leads: ETIQUETA (rapido) + RED DE SEGURIDAD por calendario (triajes recientes sin analizar,
# aunque nadie pusiera la etiqueta). --backlog = solo calendario con ventana larga.
import datetime
seen=set(); cids=[]
def add(c):
    if c and c not in seen: seen.add(c); cids.append(c)
if not BACKLOG:
    for _tag in READY_TAGS:
        page=1
        while True:
            d=csearch({"locationId":LOC,"page":page,"pageLimit":100,"filters":[{"field":"tags","operator":"eq","value":_tag}]})
            cs=d.get("contacts",[])
            for c in cs: add(c["id"])
            if len(cs)<100: break
            page+=1
# calendario: SIEMPRE (ventana corta en modo normal, DAYS en backlog) -> nada queda sin analizar.
# Incluimos citas FUTURAS (agendadas aun sin celebrar): el resumen de SETTING no necesita que la
# llamada haya ocurrido -> se genera en cuanto el lead agenda el triaje (fix 28-jul: antes solo
# procesaba citas ya pasadas, asi el setting no aparecia hasta despues de la llamada de triaje).
# La parte de TRIAJE no se adelanta: needs_triage exige transcripcion/nota, que aun no existe.
win=DAYS if BACKLOG else int(os.environ.get("SAFETY_DAYS","10"))
now=int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000); cut=now-win*86400*1000
fut=now+45*86400*1000  # incluir citas agendadas de las proximas ~6 semanas
ev=[]
for _tc in TRIAGE_CALS:
    ev+=cg(f"https://services.leadconnectorhq.com/calendars/events?locationId={LOC}&calendarId={_tc}&startTime={cut}&endTime={fut}").get("events",[])
ev_title={}  # cid -> nombre del titulo del evento (para casar Fathom si el contacto es un duplicado sin nombre)
_TKW=re.compile(r'reuni|introducci|validaci|triage|triaje|llamada|con natalie|dr\.?',re.I)
for e in ev:
    cid=e.get("contactId"); add(cid)  # futuras incluidas (setting); needs_triage sigue gateado por transcripcion
    if cid:
        segs=[s.strip() for s in re.split(r'\s*-\s*',e.get("title") or "") if s.strip()]
        nm=next((s for s in segs if not _TKW.search(s)),"")
        if nm: ev_title[cid]=nm

if not cids:
    json.dump([],open(OUT,"w"))
    print(f"Modo: {'BACKLOG' if BACKLOG else 'ETIQUETA+CAL'} | sin pendientes."); raise SystemExit(0)

# 2) Fathom: triajes con transcripcion -> mapa por nombre (solo si hay candidatos)
# created_after limita a la ventana de trabajo -> pocas paginas -> sin throttle (fix 24-jul: emails al closer vacios)
_ca=(datetime.datetime.utcnow()-datetime.timedelta(days=win+3)).strftime('%Y-%m-%dT%H:%M:%SZ')
fmap={}
for _FK in FKEYS:  # recorre cada cuenta de Fathom (David/Natalie + Christian...) y junta los triajes
    cur=None; _fails=0
    for _ in range(24):
        u=f'https://api.fathom.ai/external/v1/meetings?include_transcript=true&limit=25&created_after={_ca}'+(f'&cursor={cur}' if cur else '')
        d=cg(u,key=_FK)
        if "items" not in d:  # pagina fallida (throttle) -> reintentar, no cortar la paginacion en silencio
            _fails+=1
            if _fails>4: print("AVISO: Fathom fallo repetido, fmap parcial"); break
            time.sleep(3); continue
        for m in d.get("items",[]):
            title=m.get("title") or ""
            # triajes: "X - Triage" o "Reunion de Introduccion/Validacion - X" (excluir closing/planificacion)
            if not re.search(r'triage|triaje|introducci|validaci',title,re.I): continue
            if re.search(r'closing|planificaci|estrateg',title,re.I): continue
            KW=re.compile(r'reuni|introducci|validaci|triage|triaje|llamada',re.I)
            segs=[s.strip() for s in re.split(r'\s*-\s*',title) if s.strip()]
            lead=next((s for s in segs if not KW.search(s)),segs[0] if segs else "")
            tr=m.get("transcript") or []
            txt="\n".join(f"{(t.get('speaker') or {}).get('display_name','?')}: {t.get('text','')}" for t in tr)
            if txt and lead: fmap[nkey(lead)]={"transcript":txt[:16000],"url":m.get("share_url") or m.get("url")}
        cur=d.get("next_cursor")
        if not cur: break
cat={f["id"]:f.get("name") for f in cg(f"https://services.leadconnectorhq.com/locations/{LOC}/customFields").get("customFields",[])}

H0415=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-04-15","-H","Accept: application/json"]
def _conversacion(cid, max_msgs=120):
    """Descarga el chat REAL del setting (DM de IG/FB + WhatsApp) en orden cronologico.
    Es la UNICA fuente valida del resumen de setting: el formulario se analiza aparte
    para poder contrastar ambos (fix 5-ago-2026)."""
    try:
        r=subprocess.run(["curl","-s","-m","30","-G","https://services.leadconnectorhq.com/conversations/search",
            "--data-urlencode",f"locationId={LOC}","--data-urlencode",f"contactId={cid}",
            "--data-urlencode","limit=5",*H0415],capture_output=True,text=True).stdout
        convs=json.loads(r).get("conversations",[])
    except Exception:
        return None
    out=[]
    for c in convs:
        try:
            m=subprocess.run(["curl","-s","-m","30","-G",
                f"https://services.leadconnectorhq.com/conversations/{c['id']}/messages",
                "--data-urlencode","limit=100",*H0415],capture_output=True,text=True).stdout
            mm=json.loads(m).get("messages",{})
            msgs=mm.get("messages",[]) if isinstance(mm,dict) else (mm or [])
        except Exception:
            continue
        for x in msgs:
            if x.get("messageType") not in ("TYPE_INSTAGRAM","TYPE_FACEBOOK","TYPE_WHATSAPP","TYPE_SMS"): continue
            body=(x.get("body") or "").strip()
            if not body: continue
            out.append({"t":str(x.get("dateAdded"))[:16],
                        "quien":"SETTER" if x.get("direction")=="outbound" else "LEAD",
                        "texto":body[:800]})
    out.sort(key=lambda z:z["t"])
    if not out: return None
    out=out[-max_msgs:]
    return "\n".join(f"[{z['t']}] {z['quien']}: {z['texto']}" for z in out)

F_SETTER="lcFBOFN6VjZhvTgMFvuf"; _VS={"sary":"Sary","sara":"Sara","jesmary":"Jesmary","pablo":"Pablo","natalie":"Natalie"}
def _setter(c,tags):
    """Setter asignada: 1º utm_source del link de agenda (fiable), 2º etiquetas del contacto."""
    cf={x.get("id"):x.get("value") for x in c.get("customFields",[])}
    if (cf.get(F_SETTER) or "").strip(): return None  # ya lo tiene, no reescribir
    utm=((c.get("lastAttributionSource") or {}).get("utmSource") or (c.get("attributionSource") or {}).get("utmSource") or "").strip().lower()
    if _VS.get(utm): return _VS[utm]
    for t in (tags or []):
        tl=t.lower().replace("setter:","").strip()
        if tl in _VS: return _VS[tl]
    return None
def fetch(cid):
    c=cg(f"https://services.leadconnectorhq.com/contacts/{cid}").get("contact",{})
    notes=cg(f"https://services.leadconnectorhq.com/contacts/{cid}/notes").get("notes",[])
    cf={x.get("id"):x.get("value") for x in c.get("customFields",[])}
    tags=c.get("tags",[]) or []
    # OJO: NO excluir por DONE_TAG — un lead analizado pronto (solo setting) debe re-entrar cuando
    # llegue la transcripcion del triaje. La idempotencia la dan los campos vacios (needs_*).
    notes_txt=[strip(n.get("body")) for n in notes]
    nombre=c.get("contactName") or ((c.get("firstName") or "")+" "+(c.get("lastName") or "")).strip()
    titulo=ev_title.get(cid,"")
    if not nombre: nombre=titulo or "(sin nombre)"  # contacto duplicado sin nombre -> usa el del titulo del evento
    fa=fmap.get(nkey(nombre)) or (fmap.get(nkey(titulo)) if titulo else None)  # respaldo: casar por el titulo del evento
    has_note=any(("contexto del prospecto" in n.lower() or "fathom.video/share" in n.lower()) for n in notes_txt)
    needs_setting=not (cf.get(F_ANALISIS_SETTING) or "").strip()
    _tri=(cf.get(F_ANALISIS_TRIAJE) or "").strip()
    # vacio o "NO HAY GRABACION" cuentan como pendiente: cuando aparezca la grabacion se analiza de verdad
    needs_triage=(bool(fa) or has_note or any(t.startswith("triage-") for t in tags)) and (not _tri or _tri.upper().startswith("NO HAY GRABACION"))
    if not (needs_setting or needs_triage): return None
    filled={cat.get(k,k):v for k,v in cf.items() if v not in (None,"") and k not in NO_FORM}
    # link de la grabacion: de la API de Fathom (David/Natalie) o, si no, del link pegado en la nota (cubre triajes de Christian)
    notelink=None
    for _n in notes_txt:
        _m=re.search(r'https?://fathom\.video/(?:share|calls)/[A-Za-z0-9_-]+',_n or "")
        if _m and (notelink is None or "/share/" in _m.group(0)): notelink=_m.group(0)
    return {"contact_id":cid,"nombre":nombre,"tags":tags,
            "needs_setting":needs_setting,"needs_triage":needs_triage,
            "conversacion_setting": _conversacion(cid),   # FUENTE REAL del resumen de setting
            "campos_formulario":filled,"notas":notes_txt,
            "transcripcion_triaje": fa["transcript"] if fa else None,
            "link_triaje": (fa.get("url") if fa else None) or notelink,
            "correcciones_setting": (cf.get(F_CORR_SETTING) or "").strip() or None,
            "correcciones_triaje": (cf.get(F_CORR_TRIAJE) or "").strip() or None,
            "setter": _setter(c,tags)}
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
