#!/usr/bin/env python3
"""Rellena el campo 'Setter asignada' (lcFBOFN6VjZhvTgMFvuf) en los leads que lo tengan vacio.
Deduce el setter, por orden de fiabilidad:
  1) utm_source del link de agenda (sary/sara/pablo)  -> la fuente buena
  2) etiquetas del contacto ('setter: sary', 'sary', 'sara', 'pablo')
Solo escribe ese campo (aditivo/seguro). DRY=1 para simular."""
import subprocess,json,os,re,time,datetime
from concurrent.futures import ThreadPoolExecutor
def sec(k,f):
    return os.popen(f"grep -oE '{k}=.+' ~/.natscholibre_secrets/{f} | sed 's/{k}=//' | tr -d '\\r'").read().strip()
T=sec("GHL_TOKEN","ghl.env"); LOC=sec("GHL_LOCATION_ID","ghl.env")
DRY=os.environ.get("DRY")=="1"
H=["-H",f"Authorization: Bearer {T}","-H","Version: 2021-07-28","-H","Accept: application/json"]
HP=H+["-H","Content-Type: application/json"]
F_SETTER="lcFBOFN6VjZhvTgMFvuf"
VALID={"sary":"Sary","sara":"Sara","pablo":"Pablo","natalie":"Natalie"}
def cg(u):
    for a in range(5):
        r=subprocess.run(["curl","-s","-m","35",u,*H],capture_output=True,text=True).stdout
        if r:
            try:return json.loads(r)
            except:pass
        time.sleep(1+a)
    return {}
# universo: leads con cita de triaje/closing (los que importan para el dashboard)
# DAYS: ventana. En la automatización va corto (7 días) para no saturar la API; DAYS=180 para backfill manual.
DAYS=int(os.environ.get("DAYS","7"))
now=int(time.time()*1000); cut=now-DAYS*86400*1000
cids=set()
for cal in ["2EY5mRYqpaAx4qfnsWJM","VRaGr4KGSZNiuDamyV4q","ODbNZytVDUxJxry4QzmX"]:
    for e in cg(f"https://services.leadconnectorhq.com/calendars/events?locationId={LOC}&calendarId={cal}&startTime={cut}&endTime={now}").get("events",[]):
        if e.get("contactId"): cids.add(e["contactId"])
# + oportunidades del pipeline LEADS
for st in ["f4cc024a-c346-48ae-9068-3ed10246ed80","eeccf548-dec0-4eba-bb2d-d4e41ec65885","9e7a2fb4-06c2-4eac-8e2b-da283bc7f587",
           "53f66de1-bb59-4381-9287-623c2fa1c435","96bef4b9-c4a8-4402-b3f7-52b0744454a5","b894dab9-71df-4423-910c-e0f065c657e3",
           "2ea223eb-3e74-4d99-a842-e917cf04487c","15b4998b-e8fd-499a-b4d8-e4a4b0bb8e74","a7aef172-e89a-4cb4-ae25-0319dde7ffee"]:
    for o in cg(f"https://services.leadconnectorhq.com/opportunities/search?location_id={LOC}&pipeline_id=mW4ZfvQnRARhIlgmpj6e&pipeline_stage_id={st}&limit=100").get("opportunities",[]):
        c=(o.get("contact") or {}).get("id") or o.get("contactId")
        if c: cids.add(c)
print("leads a revisar:",len(cids),flush=True)
wrote=[0]; skip=[0]; nada=[0]; por={}
def one(cid):
    c=cg(f"https://services.leadconnectorhq.com/contacts/{cid}").get("contact",{})
    cf={x.get("id"):x.get("value") for x in c.get("customFields",[])}
    if (cf.get(F_SETTER) or "").strip(): skip[0]+=1; return
    nm=c.get("contactName") or "(sin nombre)"
    # 1) utm_source
    utm=((c.get("lastAttributionSource") or {}).get("utmSource") or (c.get("attributionSource") or {}).get("utmSource") or "").strip().lower()
    val=VALID.get(utm)
    # 2) etiquetas
    if not val:
        for t in (c.get("tags") or []):
            tl=t.lower().replace("setter:","").strip()
            if tl in VALID: val=VALID[tl]; break
    if not val: nada[0]+=1; return
    por[val]=por.get(val,0)+1
    if DRY: print(f"  [DRY] {nm[:30]:<30} -> {val}",flush=True); wrote[0]+=1; return
    for att in range(6):
        r=subprocess.run(["curl","-s","-X","PUT",f"https://services.leadconnectorhq.com/contacts/{cid}",*HP,
            "--data",json.dumps({"customFields":[{"id":F_SETTER,"value":val}]})],capture_output=True,text=True).stdout
        if '"contact"' in r: wrote[0]+=1; return
        if '429' in r: time.sleep(2.5*(att+1)); continue
        print(f"  WARN {nm[:26]}: {r[:80]}",flush=True); return
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(one,list(cids)))
print(f"\nRESULTADO: escritos={wrote[0]} · ya_tenian={skip[0]} · sin_señal={nada[0]}",flush=True)
print("por setter:",por,flush=True)
