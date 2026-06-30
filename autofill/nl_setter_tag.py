#!/usr/bin/env python3
"""Etiqueta Sara/Sary en FunnelUp/GHL segun Kommo.
Regla: lead de Kommo con la etiqueta exacta 'Sara' -> Sara; el resto de leads (que respondieron,
es decir que existen en Kommo) -> Sary. Cruce Kommo<->GHL por NOMBRE (unico dato comun).
Idempotente. Pensado para correr a diario en la nube.

Env: KOMMO_SUBDOMAIN, KOMMO_LONG_LIVED_TOKEN, GHL_TOKEN, GHL_LOCATION_ID.
SETTER_DAYS (def 60): ventana de leads recientes de Kommo. LIMIT (def 0=sin limite) para pruebas.
"""
import subprocess, os, re, json, unicodedata, datetime, urllib.parse
def env(k,f):
    v=os.environ.get(k)
    if v: return v
    b=open(os.path.expanduser(f"~/.natscholibre_secrets/{f}")).read(); return re.search(rf'{k}=(.+)',b).group(1).strip()
KSUB=env("KOMMO_SUBDOMAIN","kommo.env"); KTOK=env("KOMMO_LONG_LIVED_TOKEN","kommo.env")
GT=env("GHL_TOKEN","ghl.env"); LOC=env("GHL_LOCATION_ID","ghl.env")
KB=f"https://{KSUB}"; KH=["-H",f"Authorization: Bearer {KTOK}","-H","Accept: application/json"]
GH=["-H",f"Authorization: Bearer {GT}","-H","Version: 2021-07-28","-H","Accept: application/json"]
GHP=GH+["-H","Content-Type: application/json"]
DAYS=int(os.environ.get("SETTER_DAYS","60")); LIMIT=int(os.environ.get("LIMIT","0"))
def kget(u):
    for _ in range(3):
        r=subprocess.run(["curl","-s","-g","-m","30",u,*KH],capture_output=True,text=True).stdout
        if r:
            try: return json.loads(r)
            except: pass
    return {}
def gj(args):
    r=subprocess.run(["curl","-s","-m","30",*args],capture_output=True,text=True).stdout
    try: return json.loads(r)
    except: return {}
def norm(s):
    s=unicodedata.normalize('NFKD',(s or '').lower()); s=''.join(c for c in s if not unicodedata.combining(c))
    return [t for t in re.sub(r'[^a-z ]',' ',s).split() if len(t)>1]
def nkey(s): return " ".join(norm(s)[:3])
cut=int(datetime.datetime.now(datetime.timezone.utc).timestamp())-DAYS*86400

# 1) Kommo: leads recientes (con contactos) + contactos (para nombres)
leads=[]; page=1
while True:
    d=kget(KB+f"/api/v4/leads?with=contacts&limit=250&page={page}&filter[updated_at][from]={cut}")
    ls=(d.get("_embedded") or {}).get("leads",[]); leads+=ls
    if len(ls)<250 or page>40: break
    page+=1
contacts={}; page=1
while True:
    d=kget(KB+f"/api/v4/contacts?limit=250&page={page}&filter[updated_at][from]={cut}")
    cs=(d.get("_embedded") or {}).get("contacts",[])
    for c in cs: contacts[c["id"]]=c.get("name") or ((c.get("first_name") or "")+" "+(c.get("last_name") or "")).strip()
    if len(cs)<250 or page>40: break
    page+=1
print(f"Kommo: {len(leads)} leads recientes, {len(contacts)} contactos")

# 2) nombre -> setter (Sara si el lead tiene tag exacto 'Sara')
people={}  # nkey(nombre) -> setter
for ld in leads:
    tags=[t.get("name") for t in (ld.get("_embedded") or {}).get("tags",[])]
    setter="Sara" if "Sara" in tags else "Sary"
    for ct in (ld.get("_embedded") or {}).get("contacts",[]):
        nm=contacts.get(ct.get("id"))
        if not nm: continue
        k=nkey(nm)
        if not k: continue
        # Sara gana si hay conflicto (un contacto con varios leads)
        if k not in people or setter=="Sara": people[k]=(nm,setter)
items=list(people.items())
if LIMIT: items=items[:LIMIT]
print(f"Personas a etiquetar: {len(items)} (Sara: {sum(1 for _,v in items if v[1]=='Sara')} / Sary: {sum(1 for _,v in items if v[1]=='Sary')})")

# 3) cruce con GHL por nombre + etiquetar
def ghl_find(nombre):
    q=urllib.parse.quote(" ".join(nombre.split()[:2]))
    d=gj(["curl","-s",f"https://services.leadconnectorhq.com/contacts/?locationId={LOC}&limit=10&query={q}",*GH])
    cands=d.get("contacts",[])
    key=nkey(nombre)
    exact=[c for c in cands if nkey(c.get("contactName") or "")==key]
    pick=None
    for c in (exact or cands):
        if c.get("email") or c.get("phone"): pick=c; break
    return pick or (exact[0] if exact else (cands[0] if cands else None))
ok=0; nomatch=0
for k,(nombre,setter) in items:
    c=ghl_find(nombre)
    if not c: nomatch+=1; continue
    cid=c["id"]; tags=[t.lower() for t in (c.get("tags") or [])]
    if setter.lower() in tags:  # ya correcto
        ok+=1
    else:
        gj(["curl","-s","-X","POST",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",*GHP,"--data",json.dumps({"tags":[setter]})])
        ok+=1
    # quitar la etiqueta opuesta si la tiene
    opp="Sary" if setter=="Sara" else "Sara"
    if opp.lower() in tags:
        gj(["curl","-s","-X","DELETE",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",*GHP,"--data",json.dumps({"tags":[opp]})])
print(f"Etiquetados/confirmados: {ok} | sin match en GHL: {nomatch}")
