#!/usr/bin/env python3
"""Motor del formulario POST-CLOSING de NatschoLibre.
Lee los envios del formulario '08 Form - Tras Llamada Closing', vuelca los campos en la ficha
del lead y MUEVE la oportunidad a la etapa que corresponda. Idempotente."""
import subprocess, json, os, re, datetime
def env(k,f="ghl.env"):
    v=os.environ.get(k)
    if v: return v
    b=open(os.path.expanduser(f"~/.natscholibre_secrets/{f}")).read(); return re.search(rf'{k}=(.+)',b).group(1).strip()
T=env("GHL_TOKEN"); LOC=env("GHL_LOCATION_ID")
H=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-07-28","-H","Accept: application/json"]
HP=H+["-H","Content-Type: application/json"]
def req(m,u,body=None):
    for _ in range(3):
        c=["curl","-s","-m","45","-X",m,u,*(HP if body is not None else H)]
        if body is not None: c+=["--data",json.dumps(body)]
        r=subprocess.run(c,capture_output=True,text=True).stdout
        if r:
            try: return json.loads(r)
            except: return {"_raw":r}
    return {}

FORM="Z1S3lIrgkoedomPD0dYL"          # 08 Form - Tras Llamada Closing
PIPE="mW4ZfvQnRARhIlgmpj6e"
TAG_HECHO="closing-form-procesado"    # idempotencia
TAG_MOTOR="closing-listo"             # dispara el analisis de la grabacion

# --- campos ---
F_RES="dQQq7OBT7if2KbQv3mrx"   # Resultado closing
F_TICKET="qSSpqvVhQqBMd01jwaiB"
F_CUOTAS_IMP=["5LO6PkpFI44RmDDrmDkZ","rhC4f2kI2RXkBE74ESQx","qLWxtVHOchsja9INUiFH","19CsJ0ee2bH789jRh5aw"]
F_CUOTAS_EST=["cNLjCi5SyEiB0PLXOHBM","sta9Gni4Fm3bswwAD6tM"]   # las que tienen estado

# --- etapas: se resuelven POR NOMBRE en cada ejecucion ---
# Asi, si Jorge anade/renombra columnas (3,5k / 5k / 8k), el motor se adapta solo.
import unicodedata
def _nk(s):
    s=unicodedata.normalize("NFKD",str(s or "").lower())
    s="".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]","",s)
_ETAPAS={}
for _p in (req("GET",f"https://services.leadconnectorhq.com/opportunities/pipelines?locationId={LOC}") or {}).get("pipelines",[]):
    if _p.get("id")==PIPE:
        for _s in _p.get("stages",[]): _ETAPAS[_nk(_s.get("name"))]=_s["id"]
def etapa_id(*claves):
    """Devuelve el id de la primera etapa cuyo nombre contenga TODAS las claves."""
    for k,v in _ETAPAS.items():
        if all(_nk(c) in k for c in claves): return v
    return None

# Ofertas vivas: 3.500 / 5.000 / 8.000. Cortes en los puntos medios.
TRAMOS=[(4250,"c35k"),(6750,"c5k"),(10**9,"c8k")]   # prefijo "c" para no confundir 5k con 3,5k
def familia(ticket):
    for tope,nom in TRAMOS:
        if ticket < tope: return nom
    return "8k"

def etapa(res,ticket,pagado_todo):
    r=(res or "").strip().lower()
    if r.startswith("vendido"):
        fam=familia(ticket)                       # 35k | 5k | 8k
        estado="pagocompleto" if pagado_todo else "faltanpagos"
        st=etapa_id(fam,estado)
        if not st:                                # la columna aun no existe
            print(f"  AVISO: no encuentro la etapa '{fam}' + '{estado}'. Revisa el pipeline.")
        return st
    if "seguimiento" in r:  return etapa_id("seguimientocaliente")
    if "no show" in r or "noshow" in r: return etapa_id("clos","noshow")
    if "no confirma" in r or "cancelad" in r: return etapa_id("clos","noconfirma")
    if "descartad" in r:   return etapa_id("clos","nocualifica") or etapa_id("descartado")
    return None

def num(v):
    try: return float(re.sub(r"[^0-9.,]","",str(v)).replace(",","."))
    except: return 0.0

def etapa(res,ticket,pagado_todo):
    r=(res or "").strip().lower()
    if r.startswith("vendido"):
        # ticket >= 4.250 -> oferta de 5.000 ; por debajo -> oferta de 3.500
        alto = ticket>=CORTE_C5K
        if pagado_todo: return ST["C5K_OK"] if alto else ST["C35K_OK"]
        return ST["C5K_FALTA"] if alto else ST["C35K_FALTA"]
    if "seguimiento" in r: return ST["SEG"]
    if "no show" in r or "noshow" in r: return ST["NOSHOW"]
    if "no confirma" in r or "cancelad" in r: return ST["NOCONF"]
    if "descartad" in r: return ST["DESC"]
    return None

subs=[]; pg=1
while True:
    d=req("GET",f"https://services.leadconnectorhq.com/forms/submissions?locationId={LOC}&formId={FORM}&limit=100&page={pg}")
    s=d.get("submissions",[]) or []
    if not s: break
    subs+=s; pg+=1
    if pg>6: break
print(f"envios del formulario post-closing: {len(subs)}",flush=True)

hechos=0
for s in sorted(subs,key=lambda x:x.get("createdAt") or ""):
    o=s.get("others") or {}
    cid=s.get("contactId")
    em=str(o.get("email") or s.get("email") or "").lower().strip()
    if not cid and em:
        b=req("GET",f"https://services.leadconnectorhq.com/contacts/?locationId={LOC}&query={em}&limit=5")
        for c in b.get("contacts",[]):
            if (c.get("email") or "").lower()==em: cid=c["id"]; break
    if not cid:
        print("  SIN CONTACTO ->",em or "(sin email)"); continue
    c=(req("GET",f"https://services.leadconnectorhq.com/contacts/{cid}") or {}).get("contact",{})
    tags=[str(t).lower() for t in (c.get("tags") or [])]
    if TAG_HECHO in tags: continue          # ya procesado
    # 1) volcar TODOS los campos personalizados que traiga el envio
    cfs=[{"id":k,"value":v} for k,v in o.items()
         if re.fullmatch(r"[A-Za-z0-9]{20}",k) and str(v).strip()]
    if cfs:
        r=req("PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",{"customFields":cfs})
        if not r.get("succeeded"): print("  WARN campos",cid,str(r)[:90])
    # 2) decidir etapa
    res=str(o.get(F_RES) or ""); ticket=num(o.get(F_TICKET))
    estados=[str(o.get(k) or "").lower() for k in F_CUOTAS_EST if str(o.get(k) or "").strip()]
    importes=[num(o.get(k)) for k in F_CUOTAS_IMP if num(o.get(k))>0]
    pagado_todo = (not importes) or (estados and all("pagad" in e for e in estados))
    st=etapa(res,ticket,pagado_todo)
    if st:
        ops=(req("GET",f"https://services.leadconnectorhq.com/opportunities/search?location_id={LOC}&contact_id={cid}&limit=3") or {}).get("opportunities",[])
        if ops:
            r=req("PUT",f"https://services.leadconnectorhq.com/opportunities/{ops[0]['id']}",{"pipelineId":PIPE,"pipelineStageId":st})
            ok = bool(r.get("id") or r.get("opportunity"))
            print(f"  {c.get('firstName','')} {c.get('lastName','')} | {res} | ticket {ticket:.0f} -> {'movida' if ok else 'ERROR'}")
        else:
            print(f"  {em}: sin oportunidad que mover")
    else:
        print(f"  {em}: resultado '{res}' no mapeado, solo campos")
    req("POST",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",{"tags":[TAG_HECHO,TAG_MOTOR]})
    hechos+=1
print(f"\nprocesados: {hechos}")
