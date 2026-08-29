"""
MARKET INTELLIGENCE — PIPELINE MAESTRO (Fase 9)
Corre todo el motor y escribe a Google Sheets + results.json.
Secrets por variables de entorno: FRED_API_KEY, SHEET_ID, GSHEET_SA (json), ANTHROPIC_API_KEY (opcional).
Pensado para GitHub Actions (cron) o local.
"""
import os, json, io, time, datetime as dt
import pandas as pd, numpy as np, requests
UA={'User-Agent':'Mozilla/5.0'}
def now_iso(): return dt.datetime.now(dt.timezone.utc).isoformat()

FRED_KEY=os.environ.get('FRED_API_KEY'); SHEET_ID=os.environ.get('SHEET_ID')
SA=os.environ.get('GSHEET_SA'); SA_INFO=json.loads(SA) if SA else None
ANTHROPIC_KEY=os.environ.get('ANTHROPIC_API_KEY')

CONFIG={
 "yf":["SPY","QQQ","IWM","TLT","IEF","HYG","LQD","GLD","DBC","UUP","XLF","XLE","XLB","XLK","XLU","XLP",
       "XLY","XLI","XLV","XLRE","XLC","TIP","IWD","IWF","EEM","EFA","^VIX","HG=F","GC=F"],
 "fred":["WALCL","RRPONTSYD","WTREGEN","NFCI","ANFCI","DFII10","VIXCLS","DGS10","T10Y2Y","BAMLH0A0HYM2"],
 "cboe":["VIX3M","VIX6M"], "start":"2007-01-01"}

# ---------- INGESTA ----------
def fetch_yf(tks,start):
    import yfinance as yf; o={}
    for t in tks:
        for _ in range(3):
            try:
                d=yf.download(t,start=start,progress=False,auto_adjust=True)["Close"]
                if isinstance(d,pd.DataFrame): d=d.iloc[:,0]
                if len(d)>50: o[t]=d.rename(t); break
            except Exception: time.sleep(1.2)
    return o
def fetch_cboe(sy):
    o={}
    for s in sy:
        try:
            r=requests.get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{s}_History.csv",headers=UA,timeout=30)
            if r.status_code==200:
                df=pd.read_csv(io.StringIO(r.text)); df.columns=[c.strip().upper() for c in df.columns]
                dc=[c for c in df.columns if "DATE" in c][0]; cl=[c for c in df.columns if "CLOSE" in c]
                cc=cl[-1] if cl else [c for c in df.columns if c!=dc][-1]
                o[s]=pd.Series(pd.to_numeric(df[cc],errors="coerce").values,index=pd.to_datetime(df[dc]),name=s).dropna()
        except Exception: pass
    return o
def fetch_fred(ids,key):
    o={}
    for sid in ids:
        try:
            if key:
                from fredapi import Fred; o[sid]=Fred(api_key=key).get_series(sid).rename(sid); continue
        except Exception: pass
        try:
            r=requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",headers=UA,timeout=30)
            if r.status_code==200:
                df=pd.read_csv(io.StringIO(r.text)); df.columns=["date","val"]; df["val"]=pd.to_numeric(df["val"],errors="coerce")
                o[sid]=pd.Series(df["val"].values,index=pd.to_datetime(df["date"]),name=sid).dropna()
        except Exception: pass
    return o

# ---------- HELPERS ----------
def epct(s,m=252):
    def lp(a): a=a[~np.isnan(a)]; return (a<=a[-1]).mean()*100 if len(a) else np.nan
    return s.expanding(min_periods=m).apply(lp,raw=True)
def hyst(p,hi=55,lo=45):
    out=[];st=False
    for v in p:
        if pd.isna(v): out.append(st); continue
        st=True if(not st and v>=hi) else(False if(st and v<=lo) else st); out.append(st)
    return pd.Series(out,index=p.index)
def quad(g,i): return("Goldilocks" if not i else "Reflacion") if g else("Estanflacion" if i else "Deflacion")

def run():
    d={**fetch_fred(CONFIG["fred"],FRED_KEY),**fetch_yf(CONFIG["yf"],CONFIG["start"]),**fetch_cboe(CONFIG["cboe"])}
    P=pd.concat(d.values(),axis=1); P=P.reindex(pd.date_range(P.index.min(),P.index.max(),freq="B")).ffill(limit=5); P.columns=list(d.keys())
    R=lambda a,b: P[a]/P[b]; mom=lambda s,k: s/s.shift(k)-1
    dq=round(100*sum(1 for c in CONFIG["yf"]+CONFIG["cboe"] if c in P)/(len(CONFIG["yf"])+len(CONFIG["cboe"])),0)

    # --- regimen fundamental (6M+histeresis) + ciclo ---
    G=epct(pd.concat([mom(R("HG=F","GC=F"),126),mom(R("XLY","XLP"),126)],axis=1).mean(axis=1))
    I=epct(pd.concat([mom(R("TIP","IEF"),126),mom(R("DBC","SPY"),126)],axis=1).mean(axis=1))
    reg=pd.Series([quad(a,b) for a,b in zip(hyst(G),hyst(I))],index=P.index); cur=reg.iloc[-1]
    g,i=G.iloc[-1],I.iloc[-1]; vG=g-G.iloc[-21]; vI=i-I.iloc[-21]
    # --- market ---
    eq=epct(P["SPY"]/P["SPY"].rolling(200).mean()-1); cr=epct(mom(R("HYG","LQD"),63)); vx=100-epct(P["VIXCLS"] if "VIXCLS" in P else P["^VIX"])
    mkt=pd.concat([eq,cr,vx],axis=1).mean(axis=1); ms=mkt.iloc[-1]
    mlabel="Strong Risk-On" if ms>70 else "Risk-On" if ms>55 else "Neutral" if ms>=45 else "Risk-Off" if ms>=30 else "Strong Risk-Off"
    # --- liquidity ---
    comp=[]
    if all(c in P for c in ["WALCL","RRPONTSYD","WTREGEN"]): comp.append(epct(mom(P["WALCL"]-P["RRPONTSYD"]-P["WTREGEN"],21)))
    if "NFCI" in P: comp.append(100-epct(P["NFCI"]))
    if "DFII10" in P: comp.append(100-epct(P["DFII10"]))
    comp.append(100-epct(mom(P["UUP"],63)))
    liq=pd.concat(comp,axis=1).mean(axis=1).iloc[-1]; llabel="Easing" if liq>60 else "Neutral" if liq>=40 else "Tightening"
    # --- confirmation ---
    lead={"Goldilocks":["QQQ","XLK"],"Reflacion":["XLF","XLE","IWM","XLB"],"Estanflacion":["GLD","DBC","XLE"],"Deflacion":["TLT","XLU"]}[cur]
    lead=[l for l in lead if l in P]; RSl=(1+P[lead].pct_change().mean(axis=1)).cumprod()/(1+P["SPY"].pct_change()).cumprod()
    conf=bool(RSl.iloc[-1]>RSl.rolling(100).mean().iloc[-1])
    # --- divergence/breadth ---
    secs=[c for c in ["XLF","XLE","XLB","XLK","XLU","XLP","XLY","XLI","XLV"] if c in P]
    brd=(P[secs]>P[secs].rolling(200).mean()).mean(axis=1)*100; br1=brd.iloc[-1]-brd.iloc[-21]; spx1=(P["SPY"].iloc[-1]/P["SPY"].iloc[-21]-1)*100
    drivers={"Tendencia":eq.iloc[-1],"Credito":cr.iloc[-1],"Volatilidad":vx.iloc[-1],"Crecimiento":g,"Breadth":brd.iloc[-1]}
    agree=sum(1 for v in drivers.values() if v>=55); conf_lbl="HIGH" if agree>=4 else "MEDIUM" if agree>=3 else "LOW"
    # --- allocation (fit+mom+rs) ---
    fav={"Goldilocks":["QQQ","XLK","TLT","LQD","XLY"],"Reflacion":["XLF","XLE","XLB","IWM","DBC","HYG"],"Estanflacion":["GLD","DBC","XLE","UUP"],"Deflacion":["TLT","IEF","UUP","XLU","XLP","GLD"]}[cur]
    alloc=[]
    for a in ["SPY","QQQ","IWM","TLT","HYG","GLD","DBC","XLF","XLE","XLB","XLK","XLU"]:
        if a not in P: continue
        mo=epct(mom(P[a],63)).iloc[-1]; rs=epct(mom(P[a]/P["SPY"],63)).iloc[-1]; fit=75 if a in fav else 40
        fin=0.4*fit+0.3*mo+0.3*rs
        pos="Overweight" if fin>=62 else "Neutral" if fin>=45 else "Underweight" if fin>=35 else "Avoid"
        alloc.append(dict(asset=a,score=round(fin),positioning=pos))
    alloc=sorted(alloc,key=lambda x:-x["score"])
    # --- narrative ---
    drift="derivando hacia Goldilocks" if (cur=="Reflacion" and vI<-5 and vG>0) else "estable"
    contra="el precio sube pero la participacion (breadth) se debilita" if (spx1>0 and br1<-3) else "sin contradicciones internas mayores"
    narr=(f"Regimen {cur} (crec pctl {g:.0f} {'acelerando' if vG>0 else 'desacelerando'}, infl pctl {i:.0f} {'enfriandose' if vI<0 else 'firme'}), {drift}. "
          f"Mercado {mlabel} ({ms:.0f}). Liquidez {llabel}. Regimen {'confirmado' if conf else 'NO confirmado'} por {', '.join(lead)}. "
          f"Contradiccion: {contra}. Caso base: continuidad de {cur} mientras liderazgo y credito se sostengan. Confianza {conf_lbl}.")
    if ANTHROPIC_KEY:
        try:
            import anthropic
            facts=json.dumps(dict(regimen=cur,market=mlabel,ms=round(ms),growth=round(g),infl=round(i),conf=conf,lead=lead,contra=contra),ensure_ascii=False)
            m=anthropic.Anthropic(api_key=ANTHROPIC_KEY).messages.create(model="claude-sonnet-5",max_tokens=400,
                messages=[{"role":"user","content":f"Estratega macro. Con estos HECHOS (no cambies el regimen) escribi narrativa clara en espanol (5-7 oraciones): {facts}"}])
            narr=m.content[0].text.strip()
        except Exception as e: print("LLM off:",str(e)[:60])

    ma=P[lead[0]].rolling(200).mean().iloc[-1]
    results=dict(asof=str(P.index[-1].date()),data_quality=dq,
        confidence=dict(label=conf_lbl,agree=agree,total=5),
        regimes=dict(fundamental=dict(label=cur,transition=bool(45<=g<=55 or 45<=i<=55)),
                     market=dict(label=mlabel,score=round(ms)),liquidity=dict(label=llabel,score=round(liq))),
        cycle=dict(growth=round(g),inflation=round(i),velG=round(vG),velI=round(vI),drift=drift),
        drivers=[dict(name=k,score=round(v)) for k,v in drivers.items()],
        confirmation=dict(confirmed=conf,leaders=lead,invalidation=round(ma,2)),
        allocation=alloc, contradiction=contra,
        invalidation=[f"{lead[0]} pierde SMA200 (~{ma:.2f})","Market -> Risk-Off / credito HY se ensancha","ejes cruzan histeresis 45/55"],
        watchlist=[f"{lead[0]} SMA200","HYG/LQD","breadth","VIX term","real yields (DFII10)"],
        narrative=narr, generated_utc=now_iso())
    with open("results.json","w",encoding="utf-8") as f: json.dump(results,f,ensure_ascii=False,indent=2)
    print("Regimen:",cur,"| Market:",mlabel,f"({ms:.0f}) | Conf:",conf_lbl,"| DQ:",dq)
    print("results.json escrito.")

    if SHEET_ID and SA_INFO:
        from google.oauth2.service_account import Credentials; import gspread
        sh=gspread.authorize(Credentials.from_service_account_info(SA_INFO,scopes=["https://www.googleapis.com/auth/spreadsheets"])).open_by_key(SHEET_ID)
        def wdf(tab,df):
            try: ws=sh.worksheet(tab)
            except Exception: ws=sh.add_worksheet(title=tab,rows=max(len(df)+10,20),cols=max(len(df.columns)+2,12))
            ws.clear(); d2=df.astype(object).where(pd.notnull(df),""); ws.update([d2.columns.tolist()]+d2.values.tolist())
        DASH=pd.DataFrame([{"campo":"Fundamental","valor":cur},{"campo":"Market","valor":f"{mlabel} ({ms:.0f})"},
            {"campo":"Liquidity","valor":f"{llabel} ({liq:.0f})"},{"campo":"Cycle","valor":f"G{g:.0f}/I{i:.0f} velG{vG:+.0f} velI{vI:+.0f}"},
            {"campo":"Confirmation","valor":"CONFIRMADO" if conf else "sin confirmar"},{"campo":"Confidence","valor":f"{conf_lbl} ({agree}/5)"},
            {"campo":"Data Quality","valor":f"{dq}/100"},{"campo":"Narrativa","valor":narr},
            {"campo":"Invalidacion","valor":f"{lead[0]} < {ma:.2f}"}])
        wdf("DASHBOARD",DASH); wdf("_ALLOCATION",pd.DataFrame(alloc))
        # snapshot
        try: ws=sh.worksheet("_SNAPSHOTS")
        except Exception:
            ws=sh.add_worksheet(title="_SNAPSHOTS",rows=2000,cols=10); ws.update([["fecha","growth","infl","fundamental","market","liq","conf","dq","asof"]])
        vals=ws.get_all_values()
        row=[str(P.index[-1].date()),round(g),round(i),cur,round(ms),round(liq),int(conf),dq,now_iso()]
        if not any(r and r[0]==row[0] for r in vals[1:]): ws.append_row([str(x) for x in row])
        print("Sheets actualizado.")
    else: print("(Sin credenciales de Sheets: solo results.json)")

if __name__=="__main__": run()
