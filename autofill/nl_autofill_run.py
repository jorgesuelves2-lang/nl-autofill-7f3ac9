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
MODEL=os.environ.get("MODEL","claude-sonnet-4-6")
# 27-ago-2026: contador de fallos por lead. Un lead que falla no se marca como analizado, asi que
# volvia a entrar en CADA barrido (cada 15 min) pagando la llamada otra vez. Al 3er fallo se le pone
# 'ia-descartado' y el scan deja de proponerlo hasta que un humano quite la etiqueta.
_GTOK=env("GHL_TOKEN","ghl.env")
_GH=["-H",f"Authorization: Bearer {_GTOK}","-H","Version: 2021-07-28","-H","Content-Type: application/json"]
def marcar_fallo(cid,nombre,tags_actuales):
    t=[str(x).lower() for x in (tags_actuales or [])]
    n=sum(1 for x in t if x.startswith("ia-fallo-"))
    nueva = "ia-descartado" if n>=2 else f"ia-fallo-{n+1}"
    try:
        subprocess.run(["curl","-s","-m","20","-X","POST",
            f"https://services.leadconnectorhq.com/contacts/{cid}/tags"]+_GH+
            ["-d",json.dumps({"tags":[nueva]})],capture_output=True,text=True,timeout=30)
        if nueva=="ia-descartado":
            print(f"     -> {nombre}: 3er fallo, marcado 'ia-descartado' (no se reintentara mas)")
        else:
            print(f"     -> {nombre}: marcado '{nueva}'")
    except Exception as _e:
        print("     -> no se pudo marcar el fallo:",str(_e)[:60])
LIMIT=int(os.environ.get("LIMIT","15"))
BACKLOG="--backlog" in sys.argv

SYS=("Eres el analista de cualificacion de NatschoLibre (consultoria que ayuda a profesionales "
"latinoamericanos/espanoles a emigrar a Alemania: medicos sobre todo, pero tambien ingenieros y otros "
"profesionales, a quienes se les ofrece idioma y apoyo con papeles). Devuelves SETTING y/o TRIAJE.\n"
"\n"
"=== REGLA CRITICA SOBRE LAS FUENTES (no romper nunca) ===\n"
"El analisis de SETTING se hace SOLO con la CONVERSACION del setting (campo 'conversacion_setting') mas "
"las NOTAS del setter. El FORMULARIO ('campos_formulario') NO es fuente del resumen de setting: es una "
"fuente SEPARADA que solo se usa en el bloque final de CONTRASTE. Motivo: el negocio necesita comparar "
"lo que el lead dice EN LA CONVERSACION contra lo que puso EN EL FORMULARIO, para detectar si el "
"formulario esta mal planteado o si el setting no cualifica bien. Si mezclas ambas fuentes, ese "
"contraste se pierde y el analisis no sirve.\n"
"Si no hay conversacion disponible, dilo explicitamente en el resumen ('sin conversacion disponible') "
"en lugar de rellenar con datos del formulario.\n"
"La conversacion manda sobre el formulario cuando se contradicen: el formulario es una foto de un "
"instante, la conversacion es lo ultimo y lo mas matizado que dijo el lead.\n"
"\n"
"=== CORRECCIONES MANUALES (maxima prioridad) ===\n"
"Si llega el bloque 'CORRECCIONES MANUALES', lo ha escrito UNA PERSONA del equipo para corregirte: "
"sabe cosas que tu no puedes ver (lo que se hablo por WhatsApp o por telefono, el contexto del lead, "
"errores tuyos anteriores). MANDA SOBRE TU PROPIA LECTURA de la conversacion y de la transcripcion, y "
"tambien sobre el formulario. Si te contradice, gana la correccion y NO la discutas. Incorpora lo que "
"dice en el bloque que corresponda, con naturalidad y sin citar el nombre del campo. Si la correccion "
"cambia la cualificacion, ajusta tambien el score.\n"
"\n"
"REGLAS: (1) No inventes; usa solo lo aportado. (2) Si hay info de un setter en las notas, incorporala e "
"indica de quien es (ej. 'info de Sary'). (3) El TRIAJE se analiza SOLO con la TRANSCRIPCION de la "
"llamada de triaje (Fathom). La ficha, las notas y el formulario NUNCA son fuente del analisis de triaje: "
"si no hay transcripcion, no hay analisis de triaje. (4) Evalua cualificacion en 5 ejes: perfil, DINERO (capacidad real), decision "
"(decisor), plazo/urgencia, compromiso. (5) Da una recomendacion corta para el closer. "
"(6) info_triaje = BRIEFING ESTRUCTURADO para el closer segun el FORMATO de abajo (alimenta el email "
"pre-closing). (7) Texto en ASCII simple, sin tildes. "
"(8) Scores 0-100 basados en LA CONVERSACION (alto: profesional con dinero, decision clara, urgencia, "
"buen nivel; bajo: sin dinero, indeciso, nivel muy bajo o estudiante sin titulo).\n"
"FORMATO OBLIGATORIO de analisis_setting (Resumen Setting IA). SIEMPRE esta estructura, con estos encabezados\n"
"EXACTOS, en MAYUSCULAS, y con LINEA EN BLANCO entre bloques. NUNCA devuelvas un texto corrido sin secciones.\n"
"TODO sale de LA CONVERSACION y las notas, NUNCA del formulario. Los 3 PRIMEROS bloques son SIEMPRE estos,\n"
"en este orden. En cada uno lista TODOS los elementos que salgan de la conversacion (1 por linea con '- '):\n"
"si solo hay uno, se pone uno solo; si hay varios, se ponen todos. Si no consta ninguno, escribe 'no consta'.\n"
"1. MOTIVACIONES PRINCIPALES: por que quiere dar el paso (lo que le empuja).\n"
"\n"
"2. DOLORES PRINCIPALES: que le duele de su situacion actual (lo que quiere dejar atras).\n"
"\n"
"3. OBJETIVOS/METAS: que quiere conseguir en concreto y para cuando.\n"
"\n"
"PERFIL: profesion/titulo, pais, situacion familiar y objetivo, TAL COMO LO CONTO EL EN LA CONVERSACION.\n"
"  Incluye como entro el lead (comentario en reel, outbound del setter, etc.) si se deduce del chat.\n"
"\n"
"EJES DE CUALIFICACION (segun la conversacion):\n"
"1. PERFIL: si encaja con el avatar y por que. Si NO es medico, dilo claramente y di que se le puede ofrecer.\n"
"2. DINERO: lo que dijo EN EL CHAT sobre su capacidad de inversion. Cita sus palabras si es relevante.\n"
"3. DECISION: quien decide (solo/pareja/familia) segun lo que conto.\n"
"4. PLAZO/URGENCIA: cuando quiere avanzar y que lo condiciona.\n"
"5. COMPROMISO: senales reales del chat (rapidez de respuesta, iniciativa, si agendo, si se abrio).\n"
"\n"
"CALIDAD DEL SETTING: como lo hizo el setter. Si cualifico los 3 ejes (profesion, aleman/horas, dinero) "
"antes de agendar; si propuso la agenda; si explico el metodo o solto precio (esta prohibido); si tardo "
"mucho en responder; errores concretos (ej. llamar al lead por otro nombre). Se especifico y honesto.\n"
"\n"
"NOTA (info de [setter]): datos aportados por el setter en las notas. Omite este bloque si no hay notas.\n"
"\n"
"RIESGOS: lista breve separada por ';' de lo que puede tumbar la venta.\n"
"\n"
"CONTRASTE CON EL FORMULARIO: UNICO bloque donde se usa 'campos_formulario'. Compara lo que dijo en la\n"
"  conversacion contra lo que respondio en el formulario. Si coinciden, escribe 'sin discrepancias'.\n"
"  Si NO coinciden, di el campo, las dos versiones y cual es mas reciente/fiable (normalmente el chat).\n"
"  Este bloque sirve para saber si el formulario esta mal planteado o si el setting no cualifica bien.\n"
"\n"
"RECOMENDACION PARA SETTER: 2-3 lineas accionables (que confirmar antes de pasar a triaje).\n"
"Si un dato no consta, escribe 'no consta'.\n"
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
            f"NECESITA: setting={lead['needs_setting']} triaje={lead['needs_triage']}"]
    # 1) LA CONVERSACION primero: es la fuente del resumen de setting
    conv=lead.get("conversacion_setting")
    partes.append("=== CONVERSACION DEL SETTING (FUENTE DEL RESUMEN DE SETTING) ===")
    partes.append(conv if conv else "(sin conversacion disponible)")
    # 2) Notas del setter: complementan la conversacion
    partes.append("=== NOTAS DEL SETTER EN LA FICHA ===")
    for n in lead.get("notas",[]): partes.append(n)
    # 3) Formulario: fuente SEPARADA, solo para el bloque CONTRASTE CON EL FORMULARIO
    partes.append("=== CAMPOS DEL FORMULARIO (SOLO para el bloque CONTRASTE, no para el resumen) ===")
    for k,v in lead.get("campos_formulario",{}).items(): partes.append(f"- {k}: {v}")
    if lead.get("transcripcion_triaje"):
        partes.append("TRANSCRIPCION DEL TRIAJE (Fathom):"); partes.append(lead["transcripcion_triaje"])
    # 4) Correcciones humanas: van AL FINAL y mandan sobre todo lo anterior.
    for _k,_et in (("correcciones_setting","SETTING"),("correcciones_triaje","TRIAJE")):
        if lead.get(_k):
            partes.append(f"=== CORRECCIONES MANUALES DEL EQUIPO SOBRE EL {_et} (MANDAN SOBRE TODO LO ANTERIOR) ===")
            partes.append(lead[_k])
    instr=("\nRellena SOLO lo que se necesita (si needs_setting=false deja score_setting=0 y "
           "analisis_setting=''; si needs_triage=false deja score_triage=0, analisis_triaje='' e info_triaje='').")
    # 24-ago-2026: max_tokens estaba en 2200. Con un lead de conversacion larga (80+ mensajes) la
    # respuesta se cortaba a medias, el JSON salia incompleto y el lead se caia con KeyError.
    # Se sube el tope y se comprueba que vengan TODAS las claves antes de darlo por bueno.
    body={"model":MODEL,"max_tokens":5000,
          "system":SYS,
          "messages":[{"role":"user","content":"\n".join(partes)+instr}],
          "output_config":{"format":{"type":"json_schema","schema":SCHEMA}}}
    out=subprocess.run(["curl","-s","-m","180","-X","POST","https://api.anthropic.com/v1/messages",
      "-H",f"x-api-key: {AKEY}","-H","anthropic-version: 2023-06-01","-H","content-type: application/json",
      "--data",json.dumps(body)],capture_output=True,text=True).stdout
    d=json.loads(out or "{}")
    if not d.get("content"): raise RuntimeError("Claude: "+out[:200])
    if d.get("stop_reason")=="max_tokens": raise RuntimeError("Claude: respuesta cortada por max_tokens")
    txt=next((b["text"] for b in d["content"] if b.get("type")=="text"),"")
    r=json.loads(txt)
    faltan=[k for k in SCHEMA["required"] if k not in r]
    if faltan: raise RuntimeError("Claude: respuesta incompleta, faltan "+",".join(faltan))
    return r

# --- AHORRO (28-ago-2026): no gastar una llamada a Claude en leads que aun no han hablado ---
# Los leads que agendan solos (webinar, ads) llegan con un chat de 2-3 mensajes automaticos. Analizarlos
# con IA cuesta ~4 centimos y produce siempre lo mismo: "no se sabe nada, esta sin cualificar", que es
# informacion que YA esta en el formulario. Para esos se genera la ficha con plantilla, coste cero.
# En cuanto el lead escriba de verdad, el scan lo devuelve a la cola y se analiza con IA con normalidad.
MIN_LEAD_CHARS=int(os.environ.get("MIN_LEAD_CHARS","150"))
_AUTOMATICOS=re.compile(r'ya agend[eé] mi llamada|mi nombre es|^hola[!.\s]*$',re.I)
def chars_del_lead(conv):
    """Cuenta lo que ha escrito EL LEAD, descontando los mensajes automaticos de la landing."""
    n=0
    for linea in (conv or "").split("\n"):
        m=re.match(r'^\[[^\]]+\]\s*LEAD:\s*(.*)$',linea.strip())
        if not m: continue
        t=m.group(1).strip()
        if _AUTOMATICOS.search(t): continue
        n+=len(t)
    return n

def ficha_sin_ia(lead):
    """Resumen de un lead que aun no ha conversado: vuelca el formulario, sin llamar a la IA."""
    L=["ORIGEN: lead que agendo por su cuenta (webinar/anuncio), SIN setting previo y SIN conversacion",
       "todavia. Ficha generada automaticamente desde el formulario: NO ha intervenido la IA porque no hay",
       "nada que analizar. En cuanto el lead escriba por WhatsApp se analiza con normalidad.",""]
    cf=lead.get("campos_formulario") or {}
    if cf:
        L.append("LO QUE DECLARO EN EL FORMULARIO:")
        for k,v in cf.items():
            k2=re.sub(r'\s+',' ',str(k)).strip()
            if len(k2)>70: k2=k2[:70]+"..."
            L.append(f"- {k2}: {v}")
    else:
        L.append("LO QUE DECLARO EN EL FORMULARIO: no consta (no hay respuestas registradas).")
    esc=chars_del_lead(lead.get("conversacion_setting"))
    L+=["","LO QUE HA ESCRITO EN EL CHAT: "+("nada todavia: no pulso el boton de WhatsApp de la landing"
        if esc==0 else f"muy poco ({esc} caracteres), sin datos aprovechables"),
        "","QUE FALTA POR CUALIFICAR: todo. Profesion real, si de verdad quiere Alemania, nivel de aleman,",
        "horas de estudio, capacidad de pago, quien decide y plazo. Nadie ha hablado con este lead.",
        "","RECOMENDACION: escribirle por WhatsApp antes de la llamada y confirmar al menos profesion,",
        "destino (Alemania) y capacidad de inversion, para que el closer no llegue a ciegas."]
    return "\n".join(L)

# 1) detectar
subprocess.run(["python3",os.path.join(HERE,"nl_autofill_scan.py")]+(["--backlog"] if BACKLOG else []),check=True)
pend=json.load(open("/tmp/nl_autofill_pending.json")) if os.path.exists("/tmp/nl_autofill_pending.json") else []
pend=pend[:LIMIT]
print(f"Analizando {len(pend)} leads con {MODEL}...")
results=[]; fallos=0
for lead in pend:
    try:
        # NORMA: el triaje SOLO se analiza con la transcripcion de la llamada.
        # Sin grabacion -> no se analiza: se deja el marcador y el scan lo reintentara cuando llegue la grabacion.
        tri_sin_grabacion = lead["needs_triage"] and not lead.get("transcripcion_triaje")
        if tri_sin_grabacion: lead["needs_triage"]=False
        # Si no queda NADA que la IA pueda analizar (solo faltaba la grabacion del triaje), no se
        # llama a Claude: se marca "NO HAY GRABACION" una sola vez y se pasa al siguiente. Esto evita
        # gastar credito reanalizando en bucle a leads que nunca tendran grabacion (los de Christian).
        # (Corregido 20-ago-2026.)
        if not lead["needs_setting"] and not lead["needs_triage"]:
            if tri_sin_grabacion and not lead.get("triaje_ya_marcado"):
                r={"contact_id":lead["contact_id"],"tags":["claude-analizado"],"analisis_triaje":"NO HAY GRABACION"}
                if lead.get("link_triaje"): r["link_triaje"]=lead["link_triaje"]
                if lead.get("setter"): r["setter"]=lead["setter"]
                results.append(r); print("  MARCADO sin IA (sin grabacion)",lead["nombre"])
            else:
                print("  SKIP sin IA (sin grabacion, ya marcado)",lead["nombre"])
            continue
        # AHORRO: si solo falta el setting y el lead aun no ha escrito nada, ficha por plantilla (0 coste)
        if lead["needs_setting"] and not lead["needs_triage"] and chars_del_lead(lead.get("conversacion_setting"))<MIN_LEAD_CHARS:
            r={"contact_id":lead["contact_id"],"tags":["claude-analizado","sin-conversacion"],
               "score_setting":50,"analisis_setting":ficha_sin_ia(lead)}
            if lead.get("setter"): r["setter"]=lead["setter"]
            results.append(r); print("  SIN IA (aun no ha escrito):",lead["nombre"]); continue
        a=claude(lead); r={"contact_id":lead["contact_id"],"tags":["claude-analizado"]}
        if lead["needs_setting"]: r["score_setting"]=a["score_setting"]; r["analisis_setting"]=a["analisis_setting"]
        if tri_sin_grabacion:
            r["analisis_triaje"]="NO HAY GRABACION"
            if lead.get("link_triaje"): r["link_triaje"]=lead["link_triaje"]
        elif lead["needs_triage"]:
            r["score_triage"]=a["score_triage"]; r["analisis_triaje"]=a["analisis_triaje"]; r["info_triaje"]=a["info_triaje"]
            if lead.get("link_triaje"): r["link_triaje"]=lead["link_triaje"]
        if lead.get("setter"): r["setter"]=lead["setter"]  # rellena 'Setter asignada' si estaba vacío
        results.append(r); print("  OK",lead["nombre"])
    except Exception as e:
        fallos+=1; msg=str(e)[:200]; print("  FALLO",lead["nombre"],"->",msg[:120])
        # Si el fallo es de credito/clave, NO tiene sentido probar con el resto de leads en esta
        # ejecucion: van a fallar todos igual. Se corta SIN contar el fallo contra el lead (la culpa
        # es de la cuenta, no del lead: cuando haya credito debe reintentarse con normalidad).
        if any(k in msg.lower() for k in ("credit balance","authentication","invalid x-api-key","rate_limit")):
            print("  --> corte: problema de credito/clave de Anthropic, no se intentan mas leads.")
            break
        # Fallo propio del lead (respuesta cortada, JSON invalido, conversacion rara): se cuenta.
        marcar_fallo(lead["contact_id"],lead["nombre"],lead.get("tags"))
json.dump(results,open("/tmp/nl_autofill_results.json","w"),ensure_ascii=False)
# 2) escribir
if results:
    subprocess.run(["python3",os.path.join(HERE,"nl_autofill_write.py")],check=True)
else:
    print("Nada que escribir.")
# Alerta: si hubo fallos y no se escribio nada, salir con error para que GitHub Actions
# marque el run en rojo y avise por email (antes el motor moria en silencio con exit 0).
if fallos and not results:
    print(f"ERROR: {fallos} leads fallaron y 0 escritos — revisar credito API / conectividad.")
    sys.exit(1)
