#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Motor de PAGOS por Drive de NatschoLibre (20-ago-2026).

El closer solo hace 2 cosas: sube el justificante a la carpeta 'Justificantes de Pago'
(Closers/Closing/Justificantes de Pago) nombrado 'Nombre del Lead - DD.MM.AAAA - IMPORTEmoneda',
y anade una nota en la ficha con el link. Este motor hace el resto:
  1) lee los archivos nuevos de la carpeta (los que aun no estan en la subcarpeta 'Procesados')
  2) entiende nombre / fecha / importe del nombre del archivo
  3) encuentra al lead en GHL por nombre
  4) rellena la PRIMERA cuota libre (Importe / Estado=Pagada / Fecha de pago)
  5) recalcula el total pagado/pendiente desde TODAS las cuotas de la ficha (no solo esta)
  6) mueve la oportunidad a Faltan Pagos o Pago Completo segun corresponda
  7) anade una nota en la ficha con el link del justificante (asi al final SI queda la nota)
  8) mueve el archivo a 'Procesados' para no reprocesarlo

Si algo no se puede resolver solo (no encuentra al lead, el nombre no sigue el formato, no hay
cuota libre), el archivo se deja SIN mover y se renombra con el prefijo 'REVISAR - ' para que se
note a simple vista que necesita que alguien lo mire a mano.
"""
import subprocess, json, os, re, time, datetime, unicodedata

def env(k, f):
    v = os.environ.get(k)
    if v: return v
    p = os.path.expanduser(f"~/.natscholibre_secrets/{f}")
    if os.path.exists(p):
        m = re.search(rf'{k}=(.+)', open(p).read())
        if m: return m.group(1).strip()
    return None

GHL_TOK = env("GHL_TOKEN", "ghl.env")
LOC = env("GHL_LOCATION_ID", "ghl.env")
G_CID = env("GOOGLE_CLIENT_ID", "google_drive.env")
G_SEC = env("GOOGLE_CLIENT_SECRET", "google_drive.env")
G_RT = env("GOOGLE_REFRESH_TOKEN", "google_drive.env")

FOLDER_ID = "1Odr5x2ie5eS5QgSohCUudzTAiDV6Z4xC"   # Justificantes de Pago
PIPE = "mW4ZfvQnRARhIlgmpj6e"

H_GHL = ["-H", f"Authorization: Bearer {GHL_TOK}", "-H", "Version: 2021-07-28", "-H", "Accept: application/json"]
HP_GHL = H_GHL + ["-H", "Content-Type: application/json"]

def ghl(m, u, body=None, params=None):
    for _ in range(4):
        c = ["curl", "-s", "-m", "30", "-X", m, u, *(HP_GHL if body is not None else H_GHL)]
        if params:
            c.insert(4, "-G")
            for k, v in params.items(): c += ["--data-urlencode", f"{k}={v}"]
        if body is not None: c += ["--data", json.dumps(body, ensure_ascii=True)]
        r = subprocess.run(c, capture_output=True, text=True).stdout
        try:
            d = json.loads(r)
            if not d.get("statusCode"): return d
        except: pass
        time.sleep(2)
    return {}

def google_token():
    data = f"client_id={G_CID}&client_secret={G_SEC}&refresh_token={G_RT}&grant_type=refresh_token"
    r = subprocess.run(["curl", "-s", "-m", "20", "-X", "POST", "https://oauth2.googleapis.com/token",
                        "--data", data], capture_output=True, text=True).stdout
    return json.loads(r).get("access_token")

AT = google_token()
if not AT:
    print("ERROR: no se pudo obtener token de Google"); raise SystemExit(1)
H_DRV = ["-H", f"Authorization: Bearer {AT}"]

def drive(m, u, body=None, params=None):
    """params se envia con --data-urlencode: sin codificar, las consultas 'q' de Drive
    (que llevan comillas y espacios) devuelven vacio en silencio."""
    c = ["curl", "-s", "-m", "30", "-X", m, u, *H_DRV]
    if params:
        c.insert(4, "-G")
        for k, v in params.items(): c += ["--data-urlencode", f"{k}={v}"]
    if body is not None:
        c += ["-H", "Content-Type: application/json", "--data", json.dumps(body)]
    r = subprocess.run(c, capture_output=True, text=True).stdout
    try: return json.loads(r)
    except: return {}

def nrm(s):
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", " ", s)

# --- 1) carpeta Procesados (crear si no existe) ---
r = drive("GET", "https://www.googleapis.com/drive/v3/files", params={
    "q": f"'{FOLDER_ID}' in parents and name = 'Procesados' and mimeType = 'application/vnd.google-apps.folder' and trashed=false",
    "fields": "files(id,name)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
procf = (r.get("files") or [None])
if procf and procf[0]:
    PROCESADOS_ID = procf[0]["id"]
else:
    rr = drive("POST", "https://www.googleapis.com/drive/v3/files",
               {"name": "Procesados", "mimeType": "application/vnd.google-apps.folder", "parents": [FOLDER_ID]})
    PROCESADOS_ID = rr.get("id")
print("carpeta Procesados:", PROCESADOS_ID)

# --- 2) archivos pendientes (en el nivel superior, no en Procesados, sin el prefijo REVISAR ya marcado hoy) ---
r = drive("GET", "https://www.googleapis.com/drive/v3/files", params={
    "q": f"'{FOLDER_ID}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
    "fields": "files(id,name,webViewLink,mimeType)", "pageSize": "100",
    "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
archivos = r.get("files", [])
print(f"archivos pendientes en la carpeta: {len(archivos)}")

FECHA_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")
IMPORTE_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*(eur|usd|€|\$|euros?|dolares?)?", re.I)
PAGON_RE = re.compile(r"(\d+)\s*(?:de|/|\\)\s*(\d+)")   # "pago 1/3", "1 de 3", "pago1de3"...

def parsear_nombre(nombre):
    """'Danny Vargas - 09.07.2026 - 798USD - 2 de 5.jpg' -> (lead, fecha_iso, importe, pago_n, pago_total)
    El 4o campo (numero de pago) es OPCIONAL: si no viene, pago_n/pago_total quedan en None y el
    motor sigue usando la 'primera cuota libre' de la ficha como hasta ahora."""
    base = re.sub(r"\.\w{2,4}$", "", nombre)  # quitar extension
    partes = [p.strip() for p in base.split(" - ")]
    if len(partes) < 3: return None
    lead = partes[0]
    fm = FECHA_RE.search(partes[1])
    if not fm: return None
    d, mo, y = fm.groups()
    y = ("20" + y) if len(y) == 2 else y
    try:
        fecha = datetime.date(int(y), int(mo), int(d)).isoformat()
    except Exception:
        return None
    im = IMPORTE_RE.search(partes[2])
    if not im: return None
    importe = float(im.group(1).replace(",", "."))
    pago_n = pago_total = None
    resto = " ".join(partes[3:]) + " " + partes[2]  # el numero de pago puede ir en su propio campo o pegado al importe
    pm = PAGON_RE.search(resto)
    if pm: pago_n, pago_total = int(pm.group(1)), int(pm.group(2))
    return lead, fecha, importe, pago_n, pago_total

def buscar_contacto(nombre_lead):
    partes = [p for p in nrm(nombre_lead).split() if len(p) > 2]
    if not partes: return None
    q = " ".join(partes[:2])
    r = ghl("GET", "https://services.leadconnectorhq.com/contacts/",
            params={"locationId": LOC, "query": q, "limit": "10"})
    ws = set(partes)
    mejor = None; mejor_score = 0
    for c in r.get("contacts", []):
        # contactName suele venir vacio; usar tambien first/lastName
        nombre_c = c.get("contactName") or f"{c.get('firstName','')} {c.get('lastName','')}"
        cn = set(nrm(nombre_c).split())
        score = len(ws & cn)
        if score > mejor_score:
            mejor_score = score; mejor = c
    if mejor and mejor_score >= min(2, len(ws)):
        return mejor
    return None

CUOTAS = [("5LO6PkpFI44RmDDrmDkZ", "cNLjCi5SyEiB0PLXOHBM", "b9Zf3kUeNl8Mr58nl9JV"),
          ("rhC4f2kI2RXkBE74ESQx", "03xXAimLdWvMAkJiwqse", "wS6UCCFCAVaRfhH5wdop"),
          ("qLWxtVHOchsja9INUiFH", "ilrDZ9au5TinLOrMbZ6R", "qm0mtykxIlsT5yZW2fdp"),
          ("19CsJ0ee2bH789jRh5aw", "sta9Gni4Fm3bswwAD6tM", "lX1jqu36PG5fDkz2rZNT")]
F_TICKET = "qSSpqvVhQqBMd01jwaiB"
F_NCUOTAS = "erT6M2HhHNJ63JfjrHYI"
F_PAGADO = "Atuyg9PkXzUA0Na2OOxQ"
F_PENDIENTE = "RrCBDAhnUxtjhrboFMHP"
F_CUOTASPAG = "YG00awT7pbsbzVfXBK3v"
F_RES = "dQQq7OBT7if2KbQv3mrx"
F_ESTADO = "3se8LQQqUMP1wp6CwXSZ"

def num(v):
    try: return float(re.sub(r"[^0-9.,]", "", str(v)).replace(",", "."))
    except: return 0.0

# etapas por nombre (igual que nl_closing_form.py)
def _nk(s):
    s = unicodedata.normalize("NFKD", str(s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)
_ETAPAS = {}
for _p in ghl("GET", f"https://services.leadconnectorhq.com/opportunities/pipelines?locationId={LOC}").get("pipelines", []):
    if _p.get("id") == PIPE:
        for _s in _p.get("stages", []): _ETAPAS[_nk(_s.get("name"))] = _s["id"]
def etapa_id(*claves):
    for k, v in _ETAPAS.items():
        if all(_nk(c) in k for c in claves): return v
    return None
TRAMOS = [(4250, "c35k"), (6750, "c5k"), (10**9, "c8k")]
def familia(ticket):
    for tope, nom in TRAMOS:
        if ticket < tope: return nom
    return "8k"

def mover_a_revisar(fid, nombre_actual, motivo):
    if nombre_actual.startswith("REVISAR - "): return
    drive("PATCH", f"https://www.googleapis.com/drive/v3/files/{fid}?supportsAllDrives=true",
          {"name": f"REVISAR - {nombre_actual}"})
    print(f"  REVISAR MANUAL: {nombre_actual} -> {motivo}")

procesados_ok = 0
for a in archivos:
    nombre = a["name"]
    if nombre.startswith("REVISAR - "):
        continue  # ya marcado, esperando revision humana
    parsed = parsear_nombre(nombre)
    if not parsed:
        mover_a_revisar(a["id"], nombre, "el nombre no sigue el formato 'Lead - DD.MM.AAAA - Importe'")
        continue
    lead_nombre, fecha, importe, pago_n, pago_total = parsed
    contacto = buscar_contacto(lead_nombre)
    if not contacto:
        mover_a_revisar(a["id"], nombre, f"no se encontro ningun lead que coincida con '{lead_nombre}'")
        continue
    cid = contacto["id"]
    c = ghl("GET", f"https://services.leadconnectorhq.com/contacts/{cid}").get("contact", {})
    cf = {f["id"]: f.get("value") for f in c.get("customFields", [])}

    # si el nombre dice "2 de 5", va a la cuota 2 exacta; si no, a la primera libre
    slot = None
    if pago_n and 1 <= pago_n <= len(CUOTAS):
        slot = CUOTAS[pago_n - 1]
        if num(cf.get(slot[0])) > 0 or cf.get(slot[1]):
            mover_a_revisar(a["id"], nombre, f"la cuota {pago_n} de '{contacto.get('contactName')}' ya estaba registrada")
            continue
    else:
        for f_imp, f_est, f_fpago in CUOTAS:
            if num(cf.get(f_imp)) == 0 and not cf.get(f_est):
                slot = (f_imp, f_est, f_fpago); break
    if not slot:
        mover_a_revisar(a["id"], nombre, f"'{contacto.get('contactName')}' no tiene ninguna cuota libre (revisar a mano)")
        continue
    # si el nombre dice el total de pagos ("de 5"), guardarlo como numero de cuotas del plan
    if pago_total and num(cf.get(F_NCUOTAS)) != pago_total:
        ghl("PUT", f"https://services.leadconnectorhq.com/contacts/{cid}", {"customFields": [{"id": F_NCUOTAS, "value": pago_total}]})
        cf[F_NCUOTAS] = pago_total

    f_imp, f_est, f_fpago = slot
    escribir = [{"id": f_imp, "value": importe}, {"id": f_est, "value": "Pagada"}, {"id": f_fpago, "value": fecha}]
    ghl("PUT", f"https://services.leadconnectorhq.com/contacts/{cid}", {"customFields": escribir})
    for k, v in zip((f_imp, f_est, f_fpago), (importe, "Pagada", fecha)): cf[k] = v

    # recalcular desde TODA la ficha
    ticket = num(cf.get(F_TICKET))
    total_pagado = 0.0; cuotas_pagadas = 0; con_importe = 0
    for f_i, f_e, _ in CUOTAS:
        imp = num(cf.get(f_i))
        if imp > 0: con_importe += 1
        if "pagad" in str(cf.get(f_e) or "").lower():
            total_pagado += imp; cuotas_pagadas += 1
    pendiente = max(ticket - total_pagado, 0.0) if ticket > 0 else 0.0
    pagado_todo = (total_pagado >= ticket - 0.01) if ticket > 0 else (con_importe > 0 and cuotas_pagadas == con_importe)
    ghl("PUT", f"https://services.leadconnectorhq.com/contacts/{cid}", {"customFields": [
        {"id": F_PAGADO, "value": total_pagado}, {"id": F_PENDIENTE, "value": pendiente},
        {"id": F_CUOTASPAG, "value": cuotas_pagadas}]})

    # mover etapa (si no hay Resultado closing puesto, se asume Vendido porque esta pagando)
    res = str(cf.get(F_RES) or "").strip() or "Vendido"
    if not cf.get(F_RES):
        ghl("PUT", f"https://services.leadconnectorhq.com/contacts/{cid}", {"customFields": [
            {"id": F_RES, "value": "Vendido"},
            {"id": F_ESTADO, "value": "Pago completo" if pagado_todo else "Primer pago"}]})
    fam = familia(ticket) if ticket > 0 else "c35k"
    # 27-ago-2026: las 3 columnas de pago completo se colapsaron en una sola "Pago Completo"
    # (sin tramo de precio). Se busca con tramo y, si no existe, sin el.
    _est = "pagocompleto" if pagado_todo else "faltanpagos"
    st = etapa_id(fam, _est) or etapa_id(_est)
    if st:
        ops = ghl("GET", f"https://services.leadconnectorhq.com/opportunities/search?location_id={LOC}&contact_id={cid}&limit=3").get("opportunities", [])
        if ops:
            _body = {"pipelineId": PIPE, "pipelineStageId": st}
            # 25-ago-2026: rellenar el Value de la oportunidad con el ticket si nadie lo puso (lo lee el CAPI de Meta)
            if ticket > 0 and not (ops[0].get("monetaryValue") or 0):
                _body["monetaryValue"] = ticket
            ghl("PUT", f"https://services.leadconnectorhq.com/opportunities/{ops[0]['id']}", _body)

    # nota en la ficha, legible de un vistazo y con todo el contexto del pago
    link = a.get("webViewLink") or f"https://drive.google.com/file/d/{a['id']}/view"
    tot_txt = f"{pago_total}" if pago_total else (f"{int(num(cf.get(F_NCUOTAS)))}" if num(cf.get(F_NCUOTAS)) else "?")
    cual = f"{pago_n or cuotas_pagadas} de {tot_txt}"
    estado_txt = "PAGADO COMPLETO" if pagado_todo else f"quedan {pendiente:.2f}"
    nota = (f"PAGO {cual} recibido: {importe:.2f} el {fecha}.\n"
            f"Total pagado: {total_pagado:.2f} de {ticket:.2f} -> {estado_txt}.\n"
            f"Justificante: {link}\n"
            f"(registrado automaticamente desde la carpeta de justificantes)")
    ghl("POST", f"https://services.leadconnectorhq.com/contacts/{cid}/notes", {"body": nota})

    # mover archivo a Procesados
    drive("PATCH", f"https://www.googleapis.com/drive/v3/files/{a['id']}?addParents={PROCESADOS_ID}&removeParents={FOLDER_ID}&supportsAllDrives=true", {})
    print(f"  OK  {contacto.get('contactName')} | pago {importe:.2f} el {fecha} | pagado {total_pagado:.0f}/{ticket:.0f} | etapa {'PagoCompleto' if pagado_todo else 'FaltanPagos'}")
    procesados_ok += 1

print(f"\nprocesados: {procesados_ok}/{len(archivos)}")
