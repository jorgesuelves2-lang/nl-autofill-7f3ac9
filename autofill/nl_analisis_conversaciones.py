#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analisis diario de las conversaciones de SETTING de NatschoLibre (Instagram/Facebook DMs).

Corre en GitHub Actions y usa la API de Anthropic (credito de pago por uso), NO la suscripcion
de Claude Code. Cada ejecucion:
  1) baja las conversaciones con actividad en las ultimas HORAS horas
  2) se queda con las que tienen DMs reales de IG/FB y suficiente contenido humano
  3) puntua cada una contra la rubrica de setting (los 3 ejes + velocidad + propuesta de agenda)
  4) agrega los resultados y escribe un informe en el Drive de NatschoLibre

Diseno anti-gasto: las conversaciones ya analizadas no se vuelven a analizar (se guarda el id y la
fecha del ultimo mensaje analizado en un fichero de estado dentro del propio Drive), y hay un tope
duro de conversaciones por ejecucion.
"""
import subprocess, json, os, re, time, datetime, unicodedata
from concurrent.futures import ThreadPoolExecutor

def env(k, f):
    v = os.environ.get(k)
    if v: return v
    p = os.path.expanduser(f"~/.natscholibre_secrets/{f}")
    if os.path.exists(p):
        m = re.search(rf'{k}=(.+)', open(p).read())
        if m: return m.group(1).strip()
    return None

GHL_TOK = env("GHL_TOKEN", "ghl.env"); LOC = env("GHL_LOCATION_ID", "ghl.env")
AKEY = env("ANTHROPIC_API_KEY", "anthropic.env")
G_CID = env("GOOGLE_CLIENT_ID", "google_drive.env")
G_SEC = env("GOOGLE_CLIENT_SECRET", "google_drive.env")
G_RT  = env("GOOGLE_REFRESH_TOKEN", "google_drive.env")

MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")
HORAS = int(os.environ.get("HORAS", "24"))        # ventana de conversaciones a mirar
MAX_CONV = int(os.environ.get("MAX_CONV", "60"))  # tope duro por ejecucion (control de gasto)
MIN_MSGS_LEAD = 2                                  # minimo de mensajes del lead para que valga la pena

H = ["-H", f"Authorization: Bearer {GHL_TOK}", "-H", "Version: 2021-07-28"]

def cg(u, params=None):
    for _ in range(4):
        c = ["curl", "-s", "-m", "30", *(["-G"] if params else []), u, *H]
        if params:
            for k, v in params.items(): c += ["--data-urlencode", f"{k}={v}"]
        r = subprocess.run(c, capture_output=True, text=True).stdout
        try:
            d = json.loads(r)
            if not d.get("statusCode"): return d
        except: pass
        time.sleep(2)
    return {}

# ---------------------------------------------------------------- Drive (estado + informe)
def gtoken():
    data = f"client_id={G_CID}&client_secret={G_SEC}&refresh_token={G_RT}&grant_type=refresh_token"
    r = subprocess.run(["curl", "-s", "-m", "20", "-X", "POST", "https://oauth2.googleapis.com/token",
                        "--data", data], capture_output=True, text=True).stdout
    return json.loads(r).get("access_token")
AT = gtoken()
HD = ["-H", f"Authorization: Bearer {AT}"]
def drive(m, u, params=None, body=None, raw=None):
    c = ["curl", "-s", "-m", "40", "-X", m, u, *HD]
    if params:
        c.insert(4, "-G")
        for k, v in params.items(): c += ["--data-urlencode", f"{k}={v}"]
    if body is not None: c += ["-H", "Content-Type: application/json", "--data", json.dumps(body)]
    if raw is not None: c += ["-H", "Content-Type: text/plain; charset=utf-8", "--data-binary", raw]
    r = subprocess.run(c, capture_output=True, text=True).stdout
    try: return json.loads(r)
    except: return {}

CARPETA = os.environ.get("CARPETA_INFORMES", "")   # se resuelve/crea abajo
def carpeta_informes():
    r = drive("GET", "https://www.googleapis.com/drive/v3/files", params={
        "q": "name = 'Analisis Conversaciones Setting' and mimeType = 'application/vnd.google-apps.folder' and trashed=false",
        "fields": "files(id,name)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    fs = r.get("files") or []
    if fs: return fs[0]["id"]
    rr = drive("POST", "https://www.googleapis.com/drive/v3/files",
               body={"name": "Analisis Conversaciones Setting", "mimeType": "application/vnd.google-apps.folder"})
    return rr.get("id")

def leer_estado(fid_carpeta):
    r = drive("GET", "https://www.googleapis.com/drive/v3/files", params={
        "q": f"'{fid_carpeta}' in parents and name = 'estado.json' and trashed=false",
        "fields": "files(id,name)", "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    fs = r.get("files") or []
    if not fs: return {}, None
    fid = fs[0]["id"]
    c = ["curl", "-s", "-m", "30", "-G", f"https://www.googleapis.com/drive/v3/files/{fid}", *HD,
         "--data-urlencode", "alt=media"]
    txt = subprocess.run(c, capture_output=True, text=True).stdout
    try: return json.loads(txt), fid
    except: return {}, fid

def guardar_estado(fid_carpeta, estado, fid=None):
    contenido = json.dumps(estado, ensure_ascii=False)
    if fid:
        subprocess.run(["curl", "-s", "-m", "40", "-X", "PATCH",
                        f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media",
                        *HD, "-H", "Content-Type: application/json", "--data-binary", contenido],
                       capture_output=True, text=True)
    else:
        meta = json.dumps({"name": "estado.json", "parents": [fid_carpeta]})
        subprocess.run(["curl", "-s", "-m", "40", "-X", "POST",
                        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                        *HD, "-F", f"metadata={meta};type=application/json",
                        "-F", f"file={contenido};type=application/json"], capture_output=True, text=True)

# ---------------------------------------------------------------- conversaciones
def conversaciones_recientes():
    cut = int((time.time() - HORAS * 3600) * 1000)
    out = []; sa = None
    for _ in range(20):
        p = {"locationId": LOC, "limit": "100", "sortBy": "last_message_date", "sort": "desc"}
        if sa: p["startAfterDate"] = sa
        d = cg("https://services.leadconnectorhq.com/conversations/search", p)
        cs = d.get("conversations", [])
        if not cs: break
        stop = False
        for c in cs:
            if (c.get("lastMessageDate") or 0) < cut: stop = True; break
            out.append(c)
        if stop or len(cs) < 100: break
        sa = cs[-1].get("lastMessageDate")
    return out

def mensajes(conv_id):
    d = cg(f"https://services.leadconnectorhq.com/conversations/{conv_id}/messages?limit=100")
    return (d.get("messages") or {}).get("messages", []) or []

def transcript(msgs):
    dm = [m for m in msgs if m.get("messageType") in ("TYPE_INSTAGRAM", "TYPE_FACEBOOK")]
    dm.sort(key=lambda m: m.get("dateAdded") or "")
    lineas = []
    for m in dm:
        b = (m.get("body") or "").strip()
        if not b: continue
        quien = "SETTER" if m.get("direction") == "outbound" else "LEAD"
        lineas.append(f"[{str(m.get('dateAdded'))[:16]}] {quien}: {b}")
    n_lead = sum(1 for m in dm if m.get("direction") == "inbound" and (m.get("body") or "").strip())
    return "\n".join(lineas), n_lead

# ---------------------------------------------------------------- analisis
SYS = ("Eres auditor de calidad de SETTING de NatschoLibre, consultoria que ayuda a profesionales "
"latinoamericanos y espanoles (sobre todo MEDICOS) a emigrar a Alemania. Los setters trabajan por DM "
"de Instagram. Analizas UNA conversacion y devuelves una evaluacion objetiva.\n"
"\n"
"QUE TIENE QUE HACER UN BUEN SETTER (rubrica):\n"
"1. VELOCIDAD: responder rapido. Mas de 1 hora de demora en fase caliente es un fallo.\n"
"2. CUALIFICAR PERFIL: sacar profesion y si el titulo es de la UE o de LATAM (cambia la ruta y los plazos).\n"
"3. CUALIFICAR IDIOMA: nivel de aleman y horas que puede dedicar.\n"
"4. SEMILLA DE INVERSION: mencionar de forma natural el orden de magnitud de la inversion ANTES de "
"agendar, sin soltar el precio exacto ni preguntar a bocajarro por dinero.\n"
"5. PROPONER LA AGENDA: cerrar con una propuesta clara de llamada. No dejar la conversacion abierta.\n"
"6. NO SOLTAR PRECIO EXACTO ni explicar todo el metodo por DM (eso es trabajo de la llamada).\n"
"7. CONVERSACION NATURAL: no ir de pregunta en pregunta como un robot; validar lo que dice el lead.\n"
"8. NO PROMETER de mas: nada de empleo garantizado, homologacion express ni plazos de la ruta UE a "
"leads con titulo de LATAM.\n"
"\n"
"REGLAS: no inventes; usa solo lo que veas en la conversacion. ASCII sin tildes. Se concreto y cita "
"lo que dijeron cuando sea relevante. Si la conversacion es demasiado corta para juzgar, dilo y pon "
"score bajo de confianza.")
SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "score": {"type": "integer", "description": "0-100 calidad del setting en esta conversacion"},
    "etapa": {"type": "string", "description": "primer_contacto|cualificando|propuesta_hecha|agendada|enfriada|no_cualifica"},
    "cualifico_perfil": {"type": "boolean"},
    "cualifico_idioma": {"type": "boolean"},
    "sembro_inversion": {"type": "boolean"},
    "propuso_agenda": {"type": "boolean"},
    "bien": {"type": "string", "description": "que hizo bien, concreto"},
    "mal": {"type": "string", "description": "que hizo mal o dejo de hacer, concreto"},
    "error_grave": {"type": "string", "description": "error grave si lo hay (precio, promesa falsa, lead perdido), si no 'ninguno'"},
    "siguiente_paso": {"type": "string", "description": "que deberia hacer el setter ahora con este lead"}},
    "required": ["score", "etapa", "cualifico_perfil", "cualifico_idioma", "sembro_inversion",
                 "propuso_agenda", "bien", "mal", "error_grave", "siguiente_paso"]}

def analizar(nombre, texto):
    body = {"model": MODEL, "max_tokens": 900, "system": SYS,
            "messages": [{"role": "user", "content": f"LEAD: {nombre}\n\nCONVERSACION:\n{texto[:12000]}"}],
            "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}
    out = subprocess.run(["curl", "-s", "-m", "120", "-X", "POST", "https://api.anthropic.com/v1/messages",
                          "-H", f"x-api-key: {AKEY}", "-H", "anthropic-version: 2023-06-01",
                          "-H", "content-type: application/json", "--data", json.dumps(body)],
                         capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    if not d.get("content"): raise RuntimeError(str(d)[:200])
    return json.loads(next(b["text"] for b in d["content"] if b.get("type") == "text"))

# ---------------------------------------------------------------- main
def main():
    CARP = carpeta_informes()
    estado, fid_estado = leer_estado(CARP)
    ya = estado.get("analizadas", {})          # {conv_id: ultimo_lastMessageDate analizado}
    convs = conversaciones_recientes()
    print(f"conversaciones en las ultimas {HORAS}h: {len(convs)}")
    pend = []
    for c in convs:
        cid = c.get("id"); lmd = c.get("lastMessageDate") or 0
        if ya.get(cid) == lmd: continue        # sin novedades desde el ultimo analisis
        pend.append(c)
    print(f"con novedades desde el ultimo analisis: {len(pend)}")
    pend = pend[:MAX_CONV]
    print(f"a analizar en esta ejecucion (tope {MAX_CONV}): {len(pend)}")

    def uno(c):
        msgs = mensajes(c["id"])
        texto, n_lead = transcript(msgs)
        if n_lead < MIN_MSGS_LEAD or len(texto) < 200:
            return {"skip": True, "id": c["id"], "lmd": c.get("lastMessageDate")}
        try:
            a = analizar(c.get("fullName") or c.get("contactName") or "(sin nombre)", texto)
            a.update({"id": c["id"], "contactId": c.get("contactId"),
                      "nombre": c.get("fullName") or c.get("contactName") or "(sin nombre)",
                      "lmd": c.get("lastMessageDate")})
            return a
        except Exception as e:
            return {"error": str(e)[:120], "id": c["id"], "lmd": c.get("lastMessageDate")}

    res = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(uno, pend): res.append(r)
    buenos = [r for r in res if not r.get("skip") and not r.get("error")]
    print(f"analizadas de verdad: {len(buenos)} | descartadas por cortas: {sum(1 for r in res if r.get('skip'))} | fallos: {sum(1 for r in res if r.get('error'))}")

    # actualizar estado (tambien las descartadas, para no reintentarlas cada vuelta)
    for r in res:
        if r.get("id"): ya[r["id"]] = r.get("lmd")
    estado["analizadas"] = dict(list(ya.items())[-4000:])
    estado["ultima_ejecucion"] = datetime.datetime.utcnow().isoformat()[:19]
    guardar_estado(CARP, estado, fid_estado)

    if not buenos:
        print("nada nuevo que informar."); return

    # ------- informe agregado -------
    n = len(buenos)
    pct = lambda k: round(100.0 * sum(1 for r in buenos if r.get(k)) / n)
    media = round(sum(r["score"] for r in buenos) / n)
    from collections import Counter
    etapas = Counter(r.get("etapa") for r in buenos)
    graves = [r for r in buenos if (r.get("error_grave") or "ninguno").lower() != "ninguno"]
    peores = sorted(buenos, key=lambda r: r["score"])[:8]
    mejores = sorted(buenos, key=lambda r: -r["score"])[:3]
    hoy = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    L = []
    L.append(f"INFORME DE CALIDAD DEL SETTING — {hoy}")
    L.append(f"(conversaciones con actividad en las ultimas {HORAS}h)")
    L.append("")
    L.append(f"Conversaciones analizadas: {n}")
    L.append(f"Score medio de setting: {media}/100")
    L.append("")
    L.append("CUMPLIMIENTO DE LA RUBRICA:")
    L.append(f"  Cualifico perfil (profesion/titulo) : {pct('cualifico_perfil')}%")
    L.append(f"  Cualifico idioma (nivel/horas)      : {pct('cualifico_idioma')}%")
    L.append(f"  Sembro la inversion                 : {pct('sembro_inversion')}%")
    L.append(f"  Propuso la agenda                   : {pct('propuso_agenda')}%")
    L.append("")
    L.append("EN QUE PUNTO ESTAN:")
    for e, c in etapas.most_common(): L.append(f"  {e}: {c}")
    L.append("")
    if graves:
        L.append(f"ERRORES GRAVES DETECTADOS ({len(graves)}):")
        for r in graves[:12]:
            L.append(f"  - {r['nombre']}: {r['error_grave']}")
        L.append("")
    L.append("LAS QUE PEOR VAN (para revisar con el equipo):")
    for r in peores:
        L.append(f"  [{r['score']}] {r['nombre']} ({r['etapa']})")
        L.append(f"      falla: {r['mal']}")
        L.append(f"      siguiente paso: {r['siguiente_paso']}")
    L.append("")
    L.append("LAS QUE MEJOR VAN (para usar de ejemplo):")
    for r in mejores:
        L.append(f"  [{r['score']}] {r['nombre']}: {r['bien']}")
    informe = "\n".join(L)
    print("\n" + informe)

    # subir a Drive
    meta = json.dumps({"name": f"Informe setting {hoy}.txt", "parents": [CARP]})
    subprocess.run(["curl", "-s", "-m", "60", "-X", "POST",
                    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                    *HD, "-F", f"metadata={meta};type=application/json",
                    "-F", f"file={informe};type=text/plain"], capture_output=True, text=True)
    print(f"\ninforme subido a Drive (carpeta 'Analisis Conversaciones Setting')")

main()
