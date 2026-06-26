#!/usr/bin/env python3
"""Escribe en FunnelUp/GHL los análisis de SETTING y TRIAJE.
Lee /tmp/nl_autofill_results.json = lista de objetos con (solo lo que aplique):
  {contact_id, score_setting, analisis_setting, score_triage, analisis_triaje,
   link_triaje, nota, tags:[...]}
Escribe los campos presentes, añade nota si viene, y etiqueta 'claude-analizado'.
Codificación segura (ensure_ascii=True): evita el fallo de PUT con tildes."""
import subprocess, os, re, json
def env(k):
    v=os.environ.get(k)
    if v: return v
    b=open(os.path.expanduser("~/.natscholibre_secrets/ghl.env")).read(); return re.search(rf'{k}=(.+)',b).group(1).strip()
T=env("GHL_TOKEN")
H=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-07-28","-H","Content-Type: application/json","-H","Accept: application/json"]
F={"score_setting":"pmdl73DA4oYGPByvNdPE","analisis_setting":"bhgSTSIi5k9tCfiDQFD5",
   "score_triage":"BAdbcKq3A7Ks4kiaE9Vf","analisis_triaje":"tXb9dblrmzhtTZqdmBBj",
   "info_triaje":"N4HJDy9VFhKhGCpwJoAk",   # Información de Lead Triage (resumen que usa el email al closer)
   "link_triaje":"EC5k5nHjjV9E5Vj6kkgp"}
def curl(m,u,b=None):
    c=["curl","-s","-X",m,u,*H]
    if b is not None: c+=["--data",json.dumps(b)]  # ensure_ascii=True
    return subprocess.run(c,capture_output=True,text=True).stdout
res=json.load(open("/tmp/nl_autofill_results.json")) if os.path.exists("/tmp/nl_autofill_results.json") else []
ok=0
for r in res:
    cid=r.get("contact_id")
    if not cid: continue
    cfs=[{"id":F[k],"value":r[k]} for k in F if r.get(k) not in (None,"")]
    if cfs:
        out=curl("PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",{"customFields":cfs})
        if '"succeeded":true' not in out and '"succeded":true' not in out:
            print("WARN PUT",cid,out[:160]); continue
    if r.get("nota"):
        curl("POST",f"https://services.leadconnectorhq.com/contacts/{cid}/notes",{"body":r["nota"]})
    curl("POST",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",{"tags":r.get("tags",["claude-analizado"])})
    # quitar la etiqueta de "listo" para que no se reprocese (modo evento Nivel 2)
    curl("DELETE",f"https://services.leadconnectorhq.com/contacts/{cid}/tags",{"tags":["triaje-listo"]})
    ok+=1
    print("OK",cid,"setting" if r.get("analisis_setting") else "","triaje" if r.get("analisis_triaje") else "")
print(f"\nEscritos: {ok}/{len(res)}")
