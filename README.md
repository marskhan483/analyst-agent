# AI Analyst Agent (local)

Reads Excel / CSV / SQL -> loads into one query layer -> computes KPIs from YAML-defined metrics ->
detects anomalies -> writes insights -> builds a role-based dashboard (Executive, Sales, Finance, Operations).

## Run
    pip install -r requirements.txt
    python make_sample_data.py      # optional: creates demo Excel + SQLite files
    python agent.py run             # writes dashboard.html (open it in a browser)
    python agent.py serve           # dashboard at http://localhost:8000 with working "Ask" box

## Optional: AI insights + natural-language questions
    export ANTHROPIC_API_KEY=sk-ant-...      (Windows: setx ANTHROPIC_API_KEY ...)
    python agent.py ask "Why did EMEA revenue drop?"      (command line)
    python agent.py serve    -> use the Ask box: charts are drawn from your questions, follow-ups remember context
                                (e.g. "revenue by month" then "now by product"), and "Pin to dashboard" saves a chart.
    Every question and its SQL is logged to queries.log for audit.
Without a key, everything still works using rule-based insights.

## Use your own data
Edit config.yaml: (1) point `sources` at your Excel files / SQL URLs (e.g. postgresql+psycopg2://..., mssql+pyodbc://...),
(2) rewrite each metric's SQL so it returns `m` (YYYY-MM) and `v`, (3) choose which metrics each role sees.
Excel column names are lower-cased with spaces turned into underscores ("Order Date" -> order_date).

## Safety notes
- Sources are only read, never written. Questions run against an in-memory copy, SELECT-only.
- Insight numbers are computed in code; the model only words them. Check high-stakes figures before sharing.

## Author and license
Created by Mars Khan, 2026. Released under the MIT License (see LICENSE and NOTICE).
Reuse is welcome, but the copyright notice and license must be kept with any copy or derivative.
To cite: see CITATION.cff.
