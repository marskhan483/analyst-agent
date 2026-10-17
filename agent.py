#!/usr/bin/env python3
# Copyright (c) 2026 Mars Khan. MIT License. Original work: https://github.com/marskhan483/analyst-agent
"""AI analyst agent: Excel/CSV/SQL -> metrics -> anomaly analysis -> insights -> role dashboard.
Usage:  python agent.py run | ask "question" | serve"""
import argparse, json, os, re, sqlite3, sys, datetime as dt
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import numpy as np, pandas as pd, yaml

BASE = Path(__file__).parent
CFG = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))

# ---------- 1. INGEST ----------
def load_sources():
    con = sqlite3.connect(":memory:", check_same_thread=False)
    for s in CFG["sources"]:
        try:
            if s["type"] == "excel": df = pd.read_excel(BASE / s["path"], sheet_name=s.get("sheet", 0))
            elif s["type"] == "csv": df = pd.read_csv(BASE / s["path"])
            elif s["type"] == "sql":
                from sqlalchemy import create_engine
                df = pd.read_sql(s["query"], create_engine(s["url"].replace("{base}", str(BASE))))
            else: raise ValueError(f"unknown type {s['type']}")
        except Exception as e:
            print(f"[warn] source '{s['name']}' skipped: {e}"); continue
        df.columns = [re.sub(r"\W+", "_", str(c).strip().lower()).strip("_") for c in df.columns]
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]): df[c] = df[c].dt.strftime("%Y-%m-%d")
        df.to_sql(s["name"], con, index=False, if_exists="replace")
        print(f"[ok]   {s['name']:<10} {len(df):>6} rows")
    return con

# ---------- 2. ANALYZE ----------
def human(v, kind):
    if kind == "pct": return f"{v*100:.1f}%"
    if kind == "num": return f"{v:.2f}"
    if kind == "int": return f"{v:,.0f}"
    a = abs(v)
    return f"${v/1e6:.2f}M" if a >= 1e6 else f"${v/1e3:.0f}K" if a >= 1e3 else f"${v:.0f}"

def analyze(con):
    res = {"metrics": {}, "breakdowns": {}}
    for k, m in CFG["metrics"].items():
        try: df = pd.read_sql(m["sql"], con)
        except Exception as e: print(f"[warn] metric {k}: {e}"); continue
        if len(df) < 4: continue
        v = df["v"].astype(float).tolist(); last, prev = v[-1], v[-2]
        hist = np.array(v[:-1][-6:]); z = (last - hist.mean()) / (hist.std() or 1e-9)
        mom = (last - prev) / abs(prev) if prev else 0.0
        up = m.get("up_good", True); good = (mom >= 0) == up
        res["metrics"][k] = dict(id=k, label=m["label"], fmt=m["fmt"], labels=df["m"].tolist(), values=v, last=last, prev=prev,
            mom=mom, z=float(z), good=good, flagged=bool(abs(z) >= CFG.get("z_threshold", 1.8)), avg6=float(hist.mean()),
            shown=human(last, m["fmt"]), delta=(f"{mom*100:+.1f}% MoM" if m["fmt"] != "pct" else f"{(last-prev)*100:+.1f} pts MoM"))
    for k, b in CFG["breakdowns"].items():
        try: df = pd.read_sql(b["sql"], con)
        except Exception as e: print(f"[warn] breakdown {k}: {e}"); continue
        res["breakdowns"][k] = dict(id=k, title=b["title"], fmt=b["fmt"], up_good=b.get("up_good", True), rows=df.to_dict("records"))
    return res

def rule_insights(res, role):
    cfg = CFG["roles"][role]; out = []
    for mid in cfg["metrics"]:
        m = res["metrics"].get(mid)
        if not m: continue
        if m["flagged"] or abs(m["mom"]) > .12:
            tag = "WIN" if m["good"] else "RISK"
            out.append(dict(tag=tag, text=f"{m['label']} is {m['shown']} ({m['delta']}), {abs(m['z']):.1f} std devs "
                f"{'above' if m['z']>0 else 'below'} its 6-month average of {human(m['avg6'], m['fmt'])}."))
    b = res["breakdowns"].get(cfg.get("bar"))
    if b and b["rows"]:
        rows = sorted(b["rows"], key=lambda r: r["v"] - r["p"])
        worst, best = rows[0], rows[-1]
        if b["up_good"]:
            if worst["p"] and worst["v"] / worst["p"] - 1 < -.1:
                out.append(dict(tag="RISK", text=f"Largest decline: {worst['label']} fell {abs(worst['v']/worst['p']-1)*100:.0f}% to {human(worst['v'], b['fmt'])}. Investigate and assign an owner."))
            if best["p"] and best["v"] / best["p"] - 1 > .1:
                out.append(dict(tag="OPP", text=f"{best['label']} grew {(best['v']/best['p']-1)*100:.0f}% to {human(best['v'], b['fmt'])}. Consider doubling down."))
        elif best["p"] and best["v"] / best["p"] - 1 > .1:
            out.append(dict(tag="RISK", text=f"Biggest increase: {best['label']} rose {(best['v']/best['p']-1)*100:.0f}% to {human(best['v'], b['fmt'])}. Find the root cause and decide whether it is one-off or structural."))
    return out or [dict(tag="WIN", text="No unusual movements this period.")]

def llm(system, user, max_tokens=1500):
    import anthropic
    r = anthropic.Anthropic().messages.create(model=CFG.get("model", "claude-sonnet-5-5"), max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if b.type == "text")

def ai_insights(res, base):
    """Ask Claude to write executive-grade insights from the computed facts only."""
    if not os.environ.get("ANTHROPIC_API_KEY"): return base
    facts = {r: [i["text"] for i in base[r]] for r in base}
    try:
        txt = llm("You are a senior business analyst. Rewrite the findings per role as 2-4 crisp insights each with a concrete recommended action. "
                  "Use ONLY the numbers given; never invent figures. Return ONLY JSON: {role: [{tag: RISK|OPP|WIN, text: str}]}.", json.dumps(facts))
        data = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
        return {r: data.get(r, base[r]) for r in base}
    except Exception as e:
        print(f"[warn] AI insights failed, using rules: {e}"); return base

# ---------- 3. RENDER ----------
def build(con):
    res = analyze(con)
    base = {r: rule_insights(res, r) for r in CFG["roles"]}
    ins = ai_insights(res, base)
    payload = dict(generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"), ai=bool(os.environ.get("ANTHROPIC_API_KEY")),
                   sources=[s["name"] for s in CFG["sources"]], roles={})
    for r, c in CFG["roles"].items():
        line = res["metrics"].get(c["line"]); bar = res["breakdowns"].get(c["bar"])
        payload["roles"][r] = dict(kpis=[res["metrics"][m] for m in c["metrics"] if m in res["metrics"]],
                                   line=line, bar=bar, insights=ins[r])
    out = BASE / "dashboard.html"
    out.write_text((BASE / "template.html").read_text(encoding="utf-8").replace("__DATA__", json.dumps(payload)), encoding="utf-8")
    return out

# ---------- 4. ASK (text-to-SQL + chart, with follow-up memory) ----------
LOG = BASE / "queries.log"
BAD = re.compile(r"(?i)\b(insert|update|delete|drop|alter|attach|pragma|create|replace)\b|;")

def schema_text(con):
    s = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        s.append(f"{t}({', '.join(cols)}) e.g. {con.execute(f'SELECT * FROM {t} LIMIT 2').fetchall()}")
    return "\n".join(s)

def parse_json(txt): return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))

def run_sql(con, sql):
    sql = re.sub(r"```(sql)?", "", sql).strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", sql) or BAD.search(sql):
        raise ValueError("Blocked: only plain read-only SELECT queries are allowed.")
    return sql, pd.read_sql(sql, con)

def answer(con, q, history=()):
    """Returns {answer, sql, chart, table}. history = [{q, sql}, ...] gives follow-up context."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return dict(answer="Set ANTHROPIC_API_KEY to enable questions.", sql="", chart=None, table=[])
    metrics = "\n".join(f"- {m['label']}: {m['sql']}" for m in CFG["metrics"].values())
    system = ('You are a BI analyst. Return ONLY JSON: {"sql": str, "chart": {"type": "line|bar|table", "x": column, "y": column, '
              '"title": str, "fmt": "money|pct|int|num"}}. SQLite SELECT only; dates are \'YYYY-MM-DD\' text. '
              "When the question concerns an official metric below, reuse its definition so numbers match the dashboard. "
              "For follow-ups (e.g. 'now by product', 'only Q3'), modify the previous query. Use type table if no chart fits.\n"
              f"Official metrics:\n{metrics}\nSchema:\n{schema_text(con)}")
    ctx = "\n".join(f"Q: {h['q']}\nSQL: {h['sql']}" for h in list(history)[-4:]) or "(none)"
    user, err = f"Previous turns:\n{ctx}\n\nQuestion: {q}", None
    for _ in range(2):   # one automatic retry if the SQL fails
        spec = parse_json(llm(system, user + (f"\nYour last SQL failed: {err}. Fix it." if err else ""), 900))
        try: sql, df = run_sql(con, spec["sql"]); break
        except Exception as e: err = str(e)
    else:
        return dict(answer=f"I couldn't build a valid query for that ({err}). Try rephrasing.", sql="", chart=None, table=[])
    c, chart = spec.get("chart", {}), None
    if c.get("type") in ("line", "bar") and c.get("x") in df and c.get("y") in df and len(df):
        d = df.head(200 if c["type"] == "line" else 12)
        labels = [str(x) for x in d[c["x"]]]; vals = [float(v) for v in pd.to_numeric(d[c["y"]], errors="coerce").fillna(0)]
        chart = dict(type=c["type"], title=c.get("title", q), fmt=c.get("fmt", "num"), labels=labels, values=vals,
                     rows=[dict(label=l, v=v, p=0) for l, v in zip(labels, vals)])
        if c["type"] == "line" and len(vals) < 3: chart["type"] = "bar"
    summ = llm("Answer the business question in 2-4 sentences using only this query result. Cite numbers. No speculation about causes unless the data shows it.",
               f"Q: {q}\nSQL: {sql}\nResult:\n{df.head(30).to_string()}", 500)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(t=dt.datetime.now().isoformat(timespec="seconds"), q=q, sql=sql)) + "\n")
    return dict(answer=summ, sql=sql, chart=chart, table=json.loads(df.head(10).to_json(orient="records")))

def serve(con, port=8000):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, ctype, body):
            self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            self._send(200, "text/html; charset=utf-8", (BASE / "dashboard.html").read_bytes())
        def do_POST(self):
            if urlparse(self.path).path != "/ask": return self._send(404, "text/plain", b"not found")
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                out = answer(con, req.get("q", ""), req.get("history", []))
            except Exception as e: out = dict(answer=f"Error: {e}", sql="", chart=None, table=[])
            self._send(200, "application/json", json.dumps(out).encode())
        def log_message(self, *a): pass
    print(f"Dashboard at http://localhost:{port}  (Ctrl+C to stop)"); HTTPServer(("127.0.0.1", port), H).serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["run", "ask", "serve"]); ap.add_argument("question", nargs="?")
    a = ap.parse_args(); con = load_sources()
    if a.cmd == "ask": r = answer(con, a.question or "What changed most this month?"); print(r["answer"] + "\n\n[SQL used] " + r["sql"])
    else:
        print(f"Dashboard written to {build(con)}")
        if a.cmd == "serve": serve(con)
