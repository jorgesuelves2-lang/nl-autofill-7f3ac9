#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KPIs Setters: formulario de FunnelUp  ->  Sheet "Metricas Setters" de NatschoLibre.

Lee las respuestas del formulario "KPIs Setters" (GHL/FunnelUp) y las escribe en la
pestana de cada setter del Sheet que el equipo ya usa, respetando su formato:
las columnas A (N) y B (SEMANA) llevan formulas propias del Sheet y NO se tocan;
solo se escribe C..I (fecha + las 6 metricas).

Es idempotente: si ya hay una fila con esa fecha para esa setter, la ACTUALIZA en
lugar de anadir una nueva. Se puede ejecutar cada 15 minutos sin duplicar nada.

Uso:
    python3 nl_kpis_setters.py            # ultimos 7 dias
    python3 nl_kpis_setters.py --dias 30
    python3 nl_kpis_setters.py --dry      # no escribe, solo informa
"""
import subprocess, json, os, re, sys, datetime

sys.path.insert(0, "/Users/jorgesuelves/Desktop/Claude Code/sistema/scripts")

FORM_ID = "xYo17QaYXG2x9hFK7uyn"
SHEET_ID = "1doRZYflWNdQSoxvCqJR_hefGj6B-y0IcFhzSSQtc5IQ"

# Nombre en el formulario -> pestana del Sheet.
PESTANA = {"sary": "Sary", "sara": "Sarisa", "sarisa": "Sarisa",
           "jesmary": "Jesmary"}

# GHL devuelve las respuestas en "others" indexadas por ID de campo personalizado,
# no por nombre: por eso el mapeo va por ID (los nombres quedan como respaldo).
F_FECHA  = "H2w4exowODqlxqsYeEBC"   # Fecha del reporte
F_SETTER = "hJ0AUdYi22CUZnvb3WBT"   # Setter  ("1 - Sary")
F_NOTAS  = "NHe4iggWwz8BSg9jh3Wt"   # Notas del dia

# Orden de las columnas D..I en el Sheet, con el ID de su campo en el formulario.
METRICAS = [
    ("FOLLOW UPS",         "VNCxDXhSvsZdcdW1Mphc", ["follow ups", "followups"]),
    ("NEW CHATS INBOUND",  "zMslhPNgyrKp0pYYjG0l", ["new chats inbound", "inbound"]),
    ("OUTBOUNDS",          "LR3a7VoTUFOMZaxFRt4z", ["new chats outbound", "outbound"]),
    ("AGENDAS PROPUESTAS", "xTmXwfh7ggLHG1bavHI4", ["propuestas"]),
    ("NEW QBOOKINGS",      "5Wp6rzCeWSHDIk07Wy0D", ["bookings", "qbookings"]),
    ("WELCOME",            "MBVAn20XqrepyQHVjXxt", ["welcome"]),
]

DIAS = 7
if "--dias" in sys.argv:
    DIAS = int(sys.argv[sys.argv.index("--dias") + 1])
DRY = "--dry" in sys.argv

# ---------------------------------------------------------------- GHL
def _env(k):
    v = os.environ.get(k)
    if v:
        return v
    b = open(os.path.expanduser("~/.natscholibre_secrets/ghl.env")).read()
    return re.search(rf"{k}=(.+)", b).group(1).strip()

TOK, LOC = _env("GHL_TOKEN"), _env("GHL_LOCATION_ID")
H = ["-H", f"Authorization: Bearer {TOK}", "-H", "Version: 2021-07-28", "-H", "Accept: application/json"]


def submissions():
    """Todas las respuestas del formulario, paginando."""
    out, page = [], 1
    while True:
        u = (f"https://services.leadconnectorhq.com/forms/submissions?locationId={LOC}"
             f"&formId={FORM_ID}&limit=100&page={page}")
        r = subprocess.run(["curl", "-s", "-m", "40", u, *H], capture_output=True, text=True).stdout
        try:
            d = json.loads(r)
        except Exception:
            break
        s = d.get("submissions", [])
        out += s
        if not d.get("meta", {}).get("nextPage"):
            break
        page += 1
    return out


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def leer(sub):
    """Saca setter, fecha, metricas y notas de una respuesta del formulario."""
    otros = sub.get("others") or {}
    por_nombre = {_norm(k): v for k, v in otros.items() if isinstance(v, (str, int, float))}

    def campo(fid, alias=()):
        v = otros.get(fid)
        if v not in (None, ""):
            return v
        for a in alias:                              # respaldo por nombre
            na = _norm(a)
            for k, vv in por_nombre.items():
                if na and na in k:
                    return vv
        return None

    crudo = campo(F_SETTER, ["setter"]) or ""
    setter = _norm(re.sub(r"^\s*\d+\s*-\s*", "", str(crudo)))   # "1 - Sary" -> "sary"

    fecha = None
    for cand in (campo(F_FECHA, ["fecha"]), sub.get("createdAt")):
        if not cand:
            continue
        t = str(cand)
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
        if m:
            fecha = datetime.date(*map(int, m.groups())); break
        m = re.match(r"(\d{2})[/-](\d{2})[/-](\d{4})", t)
        if m:
            d_, mo, y = map(int, m.groups()); fecha = datetime.date(y, mo, d_); break

    def num(fid, alias):
        v = campo(fid, alias)
        if v in (None, ""):
            return 0
        try:
            return int(float(str(v).replace(",", ".")))
        except Exception:
            return 0

    return {"setter": setter, "fecha": fecha,
            "valores": [num(fid, al) for _, fid, al in METRICAS],
            "notas": (campo(F_NOTAS, ["notas"]) or ""),
            "id": sub.get("id")}


# ---------------------------------------------------------------- Sheet
# En el Mac usa el token de sistema/.credentials; en la nube (GitHub Actions) no existe ese
# fichero, asi que se construyen las credenciales desde las variables de entorno GOOGLE_*.
try:
    from gdrive import svc_sheets                               # noqa: E402
    SH = svc_sheets("natscholibre")
except Exception:
    from google.oauth2.credentials import Credentials           # noqa: E402
    from googleapiclient.discovery import build                 # noqa: E402
    _cid = os.environ.get("GOOGLE_CLIENT_ID")
    _sec = os.environ.get("GOOGLE_CLIENT_SECRET")
    _rt = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not (_cid and _sec and _rt):
        raise SystemExit("ERROR: sin credenciales de Google (ni token local ni GOOGLE_* en entorno)")
    _creds = Credentials(None, refresh_token=_rt, client_id=_cid, client_secret=_sec,
                         token_uri="https://oauth2.googleapis.com/token",
                         scopes=["https://www.googleapis.com/auth/spreadsheets"])
    SH = build("sheets", "v4", credentials=_creds, cache_discovery=False)

SERIE0 = datetime.date(1899, 12, 30)     # origen de fechas de Google Sheets


def filas_existentes(pestana):
    """{fecha: numero de fila} de lo que ya hay escrito en la pestana."""
    r = SH.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range=f"'{pestana}'!C1:C400",
        valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    mapa, primera_libre = {}, len(r) + 1
    for i, fila in enumerate(r):
        if i == 0:
            continue                                            # cabecera
        v = fila[0] if fila else ""
        if v in ("", None):
            primera_libre = min(primera_libre, i + 1)
            continue
        try:
            mapa[SERIE0 + datetime.timedelta(days=int(v))] = i + 1
        except Exception:
            pass
    return mapa, primera_libre


def escribir(pestana, fila, fecha, valores, notas=""):
    SH.spreadsheets().values().update(
        spreadsheetId=SHEET_ID, range=f"'{pestana}'!C{fila}:I{fila}",
        valueInputOption="USER_ENTERED",
        body={"values": [[fecha.strftime("%d/%m/%Y")] + valores]}).execute()
    if notas:                       # columna K, fuera de las que ya usaban
        SH.spreadsheets().values().update(
            spreadsheetId=SHEET_ID, range=f"'{pestana}'!K{fila}",
            valueInputOption="USER_ENTERED", body={"values": [[notas]]}).execute()


# ---------------------------------------------------------------- main
def main():
    corte = datetime.date.today() - datetime.timedelta(days=DIAS)
    subs = submissions()
    print(f"respuestas del formulario: {len(subs)}")

    pend = []
    for s in subs:
        r = leer(s)
        if not r["fecha"] or r["fecha"] < corte:
            continue
        p = PESTANA.get(r["setter"])
        if not p:
            print(f"  AVISO setter no reconocido: '{r['setter']}' (respuesta {r['id']})")
            continue
        r["pestana"] = p
        pend.append(r)

    if not pend:
        print("nada nuevo que volcar.")
        return

    cache = {}
    nuevas = actualizadas = 0
    for r in sorted(pend, key=lambda x: x["fecha"]):
        p = r["pestana"]
        if p not in cache:
            cache[p] = filas_existentes(p)
        mapa, libre = cache[p]
        fila = mapa.get(r["fecha"])
        accion = "actualiza" if fila else "anade"
        if not fila:
            fila = libre
            cache[p] = (mapa, libre + 1)
            mapa[r["fecha"]] = fila
        nota = f"  · nota: {r['notas'][:50]}" if r["notas"] else ""
        print(f"  {p:<10} {r['fecha']:%d/%m/%Y}  fila {fila:<4} {accion}  {r['valores']}{nota}")
        if not DRY:
            escribir(p, fila, r["fecha"], r["valores"], r["notas"])
        nuevas += accion == "anade"
        actualizadas += accion == "actualiza"

    print(f"\nRESULTADO: {nuevas} anadidas, {actualizadas} actualizadas{'  [DRY]' if DRY else ''}")


if __name__ == "__main__":
    main()
