"""Creates sample Excel + SQLite files so you can test the agent immediately."""
# Copyright (c) 2026 Mars Khan. MIT License. Original work: https://github.com/marskhan483/analyst-agent
import sqlite3, numpy as np, pandas as pd
from pathlib import Path
rng = np.random.default_rng(7)
Path("data").mkdir(exist_ok=True)
months = pd.period_range("2025-10", "2026-09", freq="M")
sales, exp, tix = [], [], []
for i, m in enumerate(months):
    last = i == len(months) - 1
    for r, base in {"NA": 520, "EMEA": 270, "APAC": 170, "LATAM": 80}.items():
        for p, share in {"Platform": .6, "Analytics": .3, "Services": .1}.items():
            amt = base * share * 1000 * (1 + .03 * i) * rng.uniform(.93, 1.07)
            if last and r == "EMEA": amt *= .68          # planted anomaly
            sales.append((m.to_timestamp() + pd.Timedelta(days=14), r, p, round(amt, 2)))
    for c, base in {"People": 400, "Cloud": 130, "Marketing": 95, "G&A": 60}.items():
        a = base * 1000 * (1 + .01 * i) * rng.uniform(.96, 1.04)
        if last and c == "Cloud": a *= 1.55              # planted anomaly
        exp.append((m.strftime("%Y-%m-15"), c, round(a, 2)))
    for d, base in {"Billing": 90, "Login": 80, "Integrations": 60, "Other": 45}.items():
        n = int(base * rng.uniform(.9, 1.1) * (1.7 if last and d == "Billing" else 1))
        tix.append((m.strftime("%Y-%m-15"), d, n, round(4.4 - (.5 if last else 0) * (d == "Billing") + rng.uniform(-.1, .1), 2)))
with pd.ExcelWriter("data/sales.xlsx") as w:
    pd.DataFrame(sales, columns=["Date", "Region", "Product", "Amount"]).to_excel(w, sheet_name="Sales", index=False)
con = sqlite3.connect("data/company.db")
pd.DataFrame(exp, columns=["date", "category", "amount"]).to_sql("expenses", con, index=False, if_exists="replace")
pd.DataFrame(tix, columns=["date", "driver", "tickets", "csat"]).to_sql("tickets", con, index=False, if_exists="replace")
print("Created data/sales.xlsx and data/company.db")
