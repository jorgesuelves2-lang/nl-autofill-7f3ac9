#!/usr/bin/env python3
"""Motor AUTONOMO de autofill del CLOSING: detecta leads con llamada de closing, la analiza con
la API de Claude y escribe el resumen/score/objeciones en la ficha de FunnelUp/GHL. Apto para cron 24/7.

Detección: contactos con cita en los calendarios de CLOSING (ultimos DAYS) cuya ficha NO tiene
'Información del Lead Closing' y que tienen transcripcion de la llamada en Fathom (match por nombre).

Secretos: variables de entorno (CI) o ~/.natscholibre_secrets/*.env (local).
LIMIT=15 por defecto.
"""
import subprocess, os, re, json, datetime, time, unicodedata, sys
from concurrent.futures import ThreadPoolExecutor
def env(k,f="ghl.env"):
    v=os.environ.get(k)
    if v: return v
    b=open(os.path.expanduser(f"~/.natscholibre_secrets/{f}")).read(); return re.search(rf'{k}=(.+)',b).group(1).strip()
T=env("GHL_TOKEN"); LOC=env("GHL_LOCATION_ID"); FKEY=env("FATHOM_API_KEY","fathom.env"); AKEY=env("ANTHROPIC_API_KEY","anthropic.env")
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
CLOSING_CALS=["VRaGr4KGSZNiuDamyV4q","ODbNZytVDUxJxry4QzmX"]
DAYS=int(os.environ.get("CLOSING_DAYS","45")); LIMIT=int(os.environ.get("LIMIT","15"))
MODEL="claude-sonnet-4-6"
# campos de closing
F_INFO="JeBTW5zL5qSdgRoUlOzu"   # Informacion del Lead Closing (resumen)
F_SCORE="Gw71M4thYl2f0qTewdnV"  # Score closing
F_OBJ="irbogxFInHAcRdPZuEPM"    # Objeciones del Lead Closing
F_MOTIVO="hTpq3AySxQLimEIlMKGp" # Motivo principal (no cierre)
F_LINK="EZqcLopGWnk2nUfMR5Yz"   # Link Llamada Closing
DONE_TAG="closing-analizado"
def cg(u,key=None):
    hdr=["-H",f"X-Api-Key: {key}"] if key else H
    for a in range(6):
        r=subprocess.run(["curl","-s","-m","40",u,*hdr],capture_output=True,text=True).stdout
        if r:
            try: return json.loads(r)
            except: pass
        time.sleep(0.6*(a+1))  # backoff creciente contra throttle de Fathom
    return {}
def strip(s):
    import html as _h
    s=re.sub(r'<br\s*/?>','\n',s or ''); s=re.sub(r'</(p|li|ul|div|tr)>','\n',s); s=re.sub(r'<[^>]+>','',s); return _h.unescape(s).strip()
def norm(s):
    s=unicodedata.normalize('NFKD',(s or '').lower()); s=''.join(c for c in s if not unicodedata.combining(c))
    s=re.sub(r'\b(ing|dr|dra|md|mg|med|odont|e-md|arg)\b','',s); return re.sub(r'[^a-z ]','',s).split()
def nkey(s): return " ".join(norm(s)[:2])
now=int(datetime.datetime.now(datetime.timezone.utc).timestamp()*1000); cut=now-DAYS*86400*1000

# 1) contactos con cita de CLOSING (pasada). Guardamos tambien el NOMBRE del titulo del evento,
# para casar la transcripcion aunque el contacto de la cita sea un duplicado SIN nombre.
cids={}; ev_title={}
_KW=re.compile(r'reuni|planificaci|estrateg|closing|dr\.?|con natalie',re.I)
for cal in CLOSING_CALS:
    for e in cg(f"https://services.leadconnectorhq.com/calendars/events?locationId={LOC}&calendarId={cal}&startTime={cut}&endTime={now}").get("events",[]):
        c=e.get("contactId")
        if not c: continue
        cids[c]=str(e.get("startTime"))[:10]
        segs=[s.strip() for s in re.split(r'\s*-\s*',e.get("title") or "") if s.strip()]
        nm=next((s for s in segs if not _KW.search(s)),"")
        if nm: ev_title[c]=nm
if not cids:
    print("Sin closings recientes."); raise SystemExit(0)

# 2) Fathom: transcripciones de CLOSING -> mapa por nombre
# created_after limita a la ventana DAYS -> pocas paginas -> sin throttle
_ca=(datetime.datetime.utcnow()-datetime.timedelta(days=DAYS+3)).strftime('%Y-%m-%dT%H:%M:%SZ')
fmap={}
for _FK in FKEYS:  # recorre cada cuenta de Fathom (David/Natalie + Christian...) y junta los closings
    cur=None; _fails=0
    for _ in range(24):
        u=f'https://api.fathom.ai/external/v1/meetings?include_transcript=true&limit=25&created_after={_ca}'+(f'&cursor={cur}' if cur else '')
        d=cg(u,key=_FK)
        if "items" not in d:  # pagina fallida (throttle) -> reintentar, no cortar la paginacion en silencio
            _fails+=1
            if _fails>8: print("AVISO: Fathom fallo repetido, fmap parcial"); break
            time.sleep(4); continue
        for m in d.get("items",[]):
            title=m.get("title") or ""
            if not re.search(r'closing|planificaci|estrateg',title,re.I): continue
            if re.search(r'triage|triaje|introducci|validaci',title,re.I): continue
            KW=re.compile(r'reuni|planificaci|estrateg|closing|dr\.?|con ',re.I)
            segs=[s.strip() for s in re.split(r'\s*-\s*',title) if s.strip()]
            lead=next((s for s in segs if not KW.search(s)),segs[0] if segs else "")
            if not lead: continue
            tr=m.get("transcript") or []
            txt="\n".join(f"{(t.get('speaker') or {}).get('display_name','?')}: {t.get('text','')}" for t in tr)
            if txt: fmap[nkey(lead)]={"transcript":txt[:18000],"url":m.get("share_url") or m.get("url"),"toks":set(norm(lead)[:4])}
        cur=d.get("next_cursor")
        if not cur: break
def match_lead(nombre):
    # 1) clave exacta de 2 tokens; 2) solape de tokens (>=2) -> robusto a nombre-vs-apellido distinto (ej. "Heber Eloy" vs "Heber Hualpa")
    fa=fmap.get(nkey(nombre))
    if fa: return fa
    nt=set(norm(nombre)[:4]); best=None; bov=1
    for v in fmap.values():
        ov=len(nt & v.get("toks",set()))
        if ov>bov: bov=ov; best=v
    return best
cat={f["id"]:f.get("name") for f in cg(f"https://services.leadconnectorhq.com/locations/{LOC}/customFields").get("customFields",[])}

def fetch(cid):
    c=cg(f"https://services.leadconnectorhq.com/contacts/{cid}").get("contact",{})
    cf={x.get("id"):x.get("value") for x in c.get("customFields",[])}
    tags=c.get("tags",[]) or []
    if DONE_TAG in tags: return None
    if (cf.get(F_INFO) or "").strip(): return None  # ya tiene resumen de closing
    nombre=c.get("contactName") or ((c.get("firstName") or "")+" "+(c.get("lastName") or "")).strip()
    fa=match_lead(nombre) or match_lead(ev_title.get(cid,""))  # respaldo: nombre del titulo del evento (contactos duplicados sin nombre)
    if not fa: return None  # sin transcripcion -> no se puede resumir
    if not nombre: nombre=ev_title.get(cid,"(sin nombre)")
    notes=cg(f"https://services.leadconnectorhq.com/contacts/{cid}/notes").get("notes",[])
    filled={cat.get(k,k):v for k,v in cf.items() if v not in (None,"") and not str(k).startswith(("Analisis","Informaci"))}
    return {"contact_id":cid,"nombre":nombre,"campos":filled,"notas":[strip(n.get("body")) for n in notes],
            "transcripcion":fa["transcript"],"url":fa["url"]}
pend=[]
with ThreadPoolExecutor(max_workers=8) as ex:
    for r in ex.map(fetch,list(cids)):
        if r: pend.append(r)
pend=pend[:LIMIT]
print(f"Closings con cita ({DAYS}d): {len(cids)} | Fathom closings: {len(fmap)} | a analizar: {len(pend)}")

SYS=("Eres analista de llamadas de CLOSING (venta) de NatschoLibre (consultoria para emigrar a Alemania como "
"medico/ingeniero; closer principal: Natalie). Te paso la transcripcion de una llamada de cierre + datos de la ficha. "
"Devuelve un analisis para Natalie. REGLAS: no inventes; ASCII sin tildes. "
"Score 0-100 de calidad/probabilidad. Objeciones: las que aparecieron. Motivo: si NO cerro, el motivo principal "
"(Dinero / Tiempo / Decisor / No convencido / Idioma-nivel / Otro). "
"FORMATO de 'info' (briefing post-closing, secciones con este encabezado exacto): "
"PERFIL: quien es y su situacion. RESULTADO: vendio / no vendio / seguimiento y por que. "
"OBJECIONES: las que salieron y como se manejaron. DINERO: capacidad y lo acordado (ticket/cuotas si consta). "
"PROXIMO PASO: accion concreta para Natalie/seguimiento. Si un dato no consta, escribe 'no consta'.")
SCHEMA={"type":"object","additionalProperties":False,"properties":{
  "info":{"type":"string"},"score":{"type":"integer"},"objeciones":{"type":"string"},"motivo":{"type":"string"}},
  "required":["info","score","objeciones","motivo"]}
def claude(p):
    blob=[f"NOMBRE: {p['nombre']}","CAMPOS FICHA:"]
    for k,v in p["campos"].items(): blob.append(f"- {k}: {v}")
    blob.append("NOTAS:"); blob+= p["notas"]
    blob.append("TRANSCRIPCION DEL CLOSING:"); blob.append(p["transcripcion"])
    body={"model":MODEL,"max_tokens":1500,"system":SYS,
          "messages":[{"role":"user","content":"\n".join(blob)}],
          "output_config":{"format":{"type":"json_schema","schema":SCHEMA}}}
    o=subprocess.run(["curl","-s","-m","120","-X","POST","https://api.anthropic.com/v1/messages",
      "-H",f"x-api-key: {AKEY}","-H","anthropic-version: 2023-06-01","-H","content-type: application/json",
      "--data",json.dumps(body)],capture_output=True,text=True).stdout
    d=json.loads(o or "{}")
    if not d.get("content"): raise RuntimeError("Claude: "+o[:200])
    return json.loads(next(b["text"] for b in d["content"] if b.get("type")=="text"))
def put(cid,fields):
    r=subprocess.run(["curl","-s","-X","PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",*HP,"--data",json.dumps({"customFields":fields})],capture_output=True,text=True).stdout
    try: ok=bool(json.loads(r).get("contact"))
    except: ok=False
    if not ok: raise RuntimeError("PUT fallo: "+r[:150])
    return r
ok=0
for p in pend:
    try:
        a=claude(p)
        put(p["contact_id"],[
            {"id":F_INFO,"value":a["info"]},
            {"id":F_SCORE,"value":a["score"]},
            {"id":F_OBJ,"value":a["objeciones"]},
            {"id":F_MOTIVO,"value":a["motivo"]},
            {"id":F_LINK,"value":p["url"] or ""},
        ])
        subprocess.run(["curl","-s","-X","POST",f"https://services.leadconnectorhq.com/contacts/{p['contact_id']}/tags",*HP,"--data",json.dumps({"tags":[DONE_TAG]})],capture_output=True,text=True)
        ok+=1; print("  OK",p["nombre"])
    except Exception as e:
        print("  FALLO",p["nombre"],"->",str(e)[:120])
print(f"Escritos: {ok}/{len(pend)}")
