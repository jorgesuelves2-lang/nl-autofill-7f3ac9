#!/usr/bin/env python3
"""Motor AUTONOMO de autofill NatschoLibre: detecta leads pendientes, los analiza con la API de
Claude (sin intervencion humana) y escribe en FunnelUp/GHL. Apto para cron/servidor 24/7.

Uso:
  python3 nl_autofill_run.py            # modo etiqueta (triaje-listo) — produccion
  python3 nl_autofill_run.py --backlog  # vacia pendientes por calendario
  LIMIT=15 python3 nl_autofill_run.py   # tope de leads por ejecucion (def. 15)
"""
import subprocess, os, re, json, sys
HERE=os.path.dirname(os.path.abspath(__file__))
def env(k,f):
    v=os.environ.get(k)
    if v: return v
    b=open(os.path.expanduser(f"~/.natscholibre_secrets/{f}")).read(); return re.search(rf'{k}=(.+)',b).group(1).strip()
AKEY=env("ANTHROPIC_API_KEY","anthropic.env")
MODEL="claude-sonnet-4-6"
LIMIT=int(os.environ.get("LIMIT","15"))
BACKLOG="--backlog" in sys.argv

SYS=("Eres el analista de cualificacion de NatschoLibre (consultoria que ayuda a profesionales "
"latinoamericanos/espanoles a emigrar a Alemania, sobre todo medicos e ingenieros). Analizas un lead "
"a partir de los datos de su formulario, las notas del setter (ej. Sary) y, si existe, la transcripcion "
"de su llamada de triaje. Devuelves SETTING y/o TRIAJE.\n"
"REGLAS: (1) No inventes; usa solo lo aportado. (2) Si hay info de un setter en las notas, incorporala e "
"indica de quien es (ej. 'info de Sary'). (3) Para TRIAJE indica la fuente: 'transcripcion de Fathom' o "
"'resumen en la ficha'. (4) Evalua cualificacion en 5 ejes: perfil, DINERO (capacidad real), decision "
"(decisor), plazo/urgencia, compromiso. Senala riesgos y, si el formulario dice 'no cualifica' pero el "
"triaje lo pasa, mencionalo. (5) Da una recomendacion corta para el closer. (6) info_triaje = BRIEFING ESTRUCTURADO "
"para el closer segun el FORMATO de abajo (alimenta el email pre-closing). (7) Texto en ASCII simple, sin tildes. "
"(8) Scores 0-100 (alto: profesional con dinero, decision clara, urgencia, buen nivel; bajo: sin dinero, "
"indeciso, nivel muy bajo o estudiante sin titulo).\n"
"FORMATO de info_triaje (briefing para el closer, 10-16 lineas, secciones con este encabezado exacto):\n"
"PERFIL: nombre, edad si consta, pais, profesion/situacion (estudiante/general/especialista) y donde obtuvo el titulo.\n"
"OBJETIVO: que busca en Alemania y plazo; nivel de aleman y horas/semana si constan.\n"
"DOLORES: por que se quiere ir (motivacion real).\n"
"INVERSION: capacidad economica (ahorro/apoyo/plan) tal como conste.\n"
"RUTA: viabilidad segun titulo (UE vs LATAM) y tiempos realistas.\n"
"OBJECIONES: dudas o frenos que salieron.\n"
"RECOMENDACION: 1-2 lineas accionables para el closer + proximo paso.\n"
"Si un dato no consta, escribe 'no consta' (no inventes).")

SCHEMA={"type":"object","additionalProperties":False,"properties":{
  "score_setting":{"type":"integer"},"analisis_setting":{"type":"string"},
  "score_triage":{"type":"integer"},"analisis_triaje":{"type":"string"},
  "info_triaje":{"type":"string"}},
  "required":["score_setting","analisis_setting","score_triage","analisis_triaje","info_triaje"]}

def claude(lead):
    partes=[f"NOMBRE: {lead['nombre']}",
            f"NECESITA: setting={lead['needs_setting']} triaje={lead['needs_triage']}",
            "CAMPOS DEL FORMULARIO:"]
    for k,v in lead.get("campos_formulario",{}).items(): partes.append(f"- {k}: {v}")
    partes.append("NOTAS DE LA FICHA:")
    for n in lead.get("notas",[]): partes.append(n)
    if lead.get("transcripcion_triaje"):
        partes.append("TRANSCRIPCION DEL TRIAJE (Fathom):"); partes.append(lead["transcripcion_triaje"])
    instr=("\nRellena SOLO lo que se necesita (si needs_setting=false deja score_setting=0 y "
           "analisis_setting=''; si needs_triage=false deja score_triage=0, analisis_triaje='' e info_triaje='').")
    body={"model":MODEL,"max_tokens":2200,
          "system":SYS,
          "messages":[{"role":"user","content":"\n".join(partes)+instr}],
          "output_config":{"format":{"type":"json_schema","schema":SCHEMA}}}
    out=subprocess.run(["curl","-s","-m","120","-X","POST","https://api.anthropic.com/v1/messages",
      "-H",f"x-api-key: {AKEY}","-H","anthropic-version: 2023-06-01","-H","content-type: application/json",
      "--data",json.dumps(body)],capture_output=True,text=True).stdout
    d=json.loads(out or "{}")
    if not d.get("content"): raise RuntimeError("Claude: "+out[:200])
    txt=next((b["text"] for b in d["content"] if b.get("type")=="text"),"")
    return json.loads(txt)

# 1) detectar
subprocess.run(["python3",os.path.join(HERE,"nl_autofill_scan.py")]+(["--backlog"] if BACKLOG else []),check=True)
pend=json.load(open("/tmp/nl_autofill_pending.json")) if os.path.exists("/tmp/nl_autofill_pending.json") else []
pend=pend[:LIMIT]
print(f"Analizando {len(pend)} leads con {MODEL}...")
results=[]
for lead in pend:
    try:
        a=claude(lead); r={"contact_id":lead["contact_id"],"tags":["claude-analizado"]}
        if lead["needs_setting"]: r["score_setting"]=a["score_setting"]; r["analisis_setting"]=a["analisis_setting"]
        if lead["needs_triage"]:
            r["score_triage"]=a["score_triage"]; r["analisis_triaje"]=a["analisis_triaje"]; r["info_triaje"]=a["info_triaje"]
            if lead.get("link_triaje"): r["link_triaje"]=lead["link_triaje"]
        results.append(r); print("  OK",lead["nombre"])
    except Exception as e:
        print("  FALLO",lead["nombre"],"->",str(e)[:120])
json.dump(results,open("/tmp/nl_autofill_results.json","w"),ensure_ascii=False)
# 2) escribir
if results:
    subprocess.run(["python3",os.path.join(HERE,"nl_autofill_write.py")],check=True)
else:
    print("Nada que escribir.")
