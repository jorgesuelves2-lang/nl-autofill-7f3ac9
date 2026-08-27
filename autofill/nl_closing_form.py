#!/usr/bin/env python3
"""Motor del formulario POST-CLOSING de NatschoLibre.
Lee los envios del formulario '08 Form - Tras Llamada Closing', vuelca los campos en la ficha
del lead y MUEVE la oportunidad a la etapa que corresponda.

IDEMPOTENCIA POR ENVIO (no por lead, 20-ago-2026): antes se marcaba el CONTACTO como "procesado"
la primera vez y cualquier envio posterior para ese mismo lead se ignoraba -> un segundo pago
(cuota 2, 3...) nunca se registraba si alguien volvia a rellenar el formulario. Ahora se recuerda
que ENVIOS concretos (por su id) ya se procesaron, en el campo "IDs envios closing procesados"
de la propia ficha, asi que el mismo lead puede tener varios envios (uno por pago) y todos cuentan.

TOTALES RECALCULADOS DESDE LA FICHA COMPLETA (no solo desde el envio nuevo): un envio de
seguimiento puede traer solo los datos de la cuota 2 sin repetir la cuota 1; el motor relee las
4 cuotas ya guardadas en la ficha, suma lo pagado, calcula el pendiente y decide si esta completo.
"""
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
TAG_RECIBIDO="closing-form-procesado"  # informativo (ya no se usa para saltar envios)
TAG_MOTOR="closing-listo"              # dispara el analisis de la grabacion
F_PROCESADOS="YlLSUZzrixbZVD7HztT0"    # IDs envios closing procesados (uso interno, no editar a mano)
MAX_IDS_GUARDADOS=30                   # tope de ids que se conservan en el campo (evita crecer sin fin)

# --- campos del formulario/ficha ---
F_RES="dQQq7OBT7if2KbQv3mrx"       # Resultado closing
F_TICKET="qSSpqvVhQqBMd01jwaiB"    # Ticket total
F_PAGADO="Atuyg9PkXzUA0Na2OOxQ"    # Importe total pagado (se recalcula, no hace falta rellenarlo a mano)
F_PENDIENTE="RrCBDAhnUxtjhrboFMHP" # Importe pendiente (se recalcula)
F_NCUOTAS="erT6M2HhHNJ63JfjrHYI"   # Numero de cuotas
F_CUOTASPAG="YG00awT7pbsbzVfXBK3v" # Cuotas pagadas (se recalcula)
# las 4 cuotas: (importe, estado) -- fecha_prevista/fecha_pago se guardan tal cual llegan, no se recalculan
CUOTAS=[("5LO6PkpFI44RmDDrmDkZ","cNLjCi5SyEiB0PLXOHBM"),   # Cuota 1
        ("rhC4f2kI2RXkBE74ESQx","03xXAimLdWvMAkJiwqse"),   # Cuota 2
        ("qLWxtVHOchsja9INUiFH","ilrDZ9au5TinLOrMbZ6R"),   # Cuota 3
        ("19CsJ0ee2bH789jRh5aw","sta9Gni4Fm3bswwAD6tM")]   # Cuota 4

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

# Ofertas vivas: 3.500 / 5.000 / 8.000 EUR. Los que mas se venden ahora son 3.500 y 5.000.
# Cortes en los puntos medios para clasificar por ticket.
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
        # 27-ago-2026: Jorge colapso las 3 columnas de pago completo en una sola "Pago Completo"
        # (sin el tramo de precio delante). Se busca primero CON tramo -por si algun estado sigue
        # separado por precio, como hoy pasa con "Faltan Pagos"- y si no existe, sin tramo.
        st=etapa_id(fam,estado) or etapa_id(estado)
        if not st:                                # la columna no existe de ninguna de las dos formas
            print(f"  AVISO: no encuentro etapa para '{estado}' (ni con tramo '{fam}' ni sin el). Revisa el pipeline.")
        return st
    if "seguimiento" in r:  return etapa_id("seguimientocaliente")
    if "no show" in r or "noshow" in r: return etapa_id("clos","noshow")
    if "no confirma" in r or "cancelad" in r: return etapa_id("clos","noconfirma")
    if "descartad" in r:   return etapa_id("clos","nocualifica") or etapa_id("descartado")
    return None

def num(v):
    try: return float(re.sub(r"[^0-9.,]","",str(v)).replace(",","."))
    except: return 0.0

def calcular_totales(cf):
    """Recalcula pagado/pendiente/cuotas_pagadas a partir de TODAS las cuotas guardadas en la
    ficha (no solo las del envio actual), para que un envio parcial (solo la cuota nueva) no
    borre el progreso de las cuotas anteriores."""
    ticket=num(cf.get(F_TICKET))
    total_pagado=0.0; cuotas_pagadas=0; cuotas_con_importe=0
    for f_imp,f_est in CUOTAS:
        imp=num(cf.get(f_imp))
        if imp>0: cuotas_con_importe+=1
        if "pagad" in str(cf.get(f_est) or "").lower():
            total_pagado+=imp; cuotas_pagadas+=1
    pendiente=max(ticket-total_pagado,0.0) if ticket>0 else 0.0
    if ticket>0:
        pagado_todo = total_pagado >= ticket-0.01
    else:
        # sin ticket declarado: solo se considera completo si hay cuotas y todas estan pagadas
        pagado_todo = cuotas_con_importe>0 and cuotas_pagadas==cuotas_con_importe
    return ticket,total_pagado,pendiente,cuotas_pagadas,pagado_todo

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
    sub_id=str(s.get("id") or "")
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
    cf={f["id"]:f.get("value") for f in c.get("customFields",[])}
    procesados=set(x.strip() for x in str(cf.get(F_PROCESADOS) or "").split(",") if x.strip())
    if sub_id and sub_id in procesados:
        continue  # este envio concreto ya se proceso; otros envios del MISMO lead si se procesan
    # 1) volcar los campos personalizados que traiga el envio (no pisa los que no vienen en este envio)
    cfs=[{"id":k,"value":v} for k,v in o.items()
         if re.fullmatch(r"[A-Za-z0-9]{20}",k) and str(v).strip()]
    if cfs:
        r=req("PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",{"customFields":cfs})
        if not r.get("succeeded"): print("  WARN campos",cid,str(r)[:90])
        for x in cfs: cf[x["id"]]=x["value"]  # reflejar el cambio en memoria para el recalculo
    # 2) recalcular totales desde la ficha COMPLETA (todas las cuotas, no solo las de este envio)
    ticket,total_pagado,pendiente,cuotas_pagadas,pagado_todo=calcular_totales(cf)
    recalc=[{"id":F_PAGADO,"value":total_pagado},{"id":F_PENDIENTE,"value":pendiente},
            {"id":F_CUOTASPAG,"value":cuotas_pagadas}]
    req("PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",{"customFields":recalc})
    # 3) decidir etapa. Si el envio no trae Resultado (p.ej. "solo apunto el pago 2") pero SI hay
    #    dinero pagado, se asume que sigue siendo una venta en curso (Vendido implicito).
    res=str(o.get(F_RES) or "").strip()
    if not res and total_pagado>0: res="Vendido"
    st=etapa(res,ticket,pagado_todo)
    if st:
        ops=(req("GET",f"https://services.leadconnectorhq.com/opportunities/search?location_id={LOC}&contact_id={cid}&limit=3") or {}).get("opportunities",[])
        if ops:
            _body={"pipelineId":PIPE,"pipelineStageId":st}
            # 25-ago-2026 (pedido por Jorge): si NADIE puso el Value de la oportunidad, se rellena con el
            # ticket del formulario post-closing. Ese Value es el importe que el bloque de Conversions API
            # de FunnelUp manda a Meta en el evento Purchase. Nunca se pisa un valor puesto a mano.
            if ticket>0 and not (ops[0].get("monetaryValue") or 0):
                _body["monetaryValue"]=ticket
            r=req("PUT",f"https://services.leadconnectorhq.com/opportunities/{ops[0]['id']}",_body)
            ok = bool(r.get("id") or r.get("opportunity"))
            print(f"  {c.get('firstName','')} {c.get('lastName','')} | {res} | pagado {total_pagado:.0f}/{ticket:.0f} -> {'movida' if ok else 'ERROR'}")
        else:
            print(f"  {em}: sin oportunidad que mover")
    else:
        print(f"  {em}: resultado '{res}' no mapeado, solo campos")
    # 4) marcar ESTE envio como procesado (no el lead entero)
    nuevos=list(procesados)+([sub_id] if sub_id else [])
    nuevos=nuevos[-MAX_IDS_GUARDADOS:]
    req("PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",{"customFields":[{"id":F_PROCESADOS,"value":",".join(nuevos)}]})
    req("POST",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",{"tags":[TAG_RECIBIDO,TAG_MOTOR]})
    hechos+=1
print(f"\nprocesados: {hechos}")
