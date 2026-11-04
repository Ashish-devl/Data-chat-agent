# Demo video script (about 4 minutes)

Everything shown uses the made-up sample data, so the video can be shared publicly.

## Before recording

```bash
docker compose up -d db redis
python eval/datasets/erp_demo.py postgresql://datachat:datachat@localhost:5432/postgres
python eval/datasets/make_pdfs.py demo_pdfs
datachat-agent create-tenant "Knitwell Demo" --owner-email demo@example.com
datachat-agent serve
```

- Use `LLM_REASONING_EFFORT=none` for quick answers, and check `datachat-agent check-llm` first.
- Browser at 1280×800, zoom 110%, dark mode off, notifications off.
- Open http://localhost:8000/console and sign in. Have `demo_pdfs/` open in a file window.

## Scenes

| # | Time | Show | Say |
| --- | --- | --- | --- |
| 1 | 0:00 | The console overview | "DataChat Agent answers plain-English questions from your SQL database and your PDFs, and shows where every answer came from." |
| 2 | 0:15 | **Databases → Connect**: `postgresql://erp_readonly:erp_readonly@localhost:5432/erp_demo` → *Connect and test* → `ok, read-only` | "It only ever reads. A user that can write is refused." |
| 3 | 0:35 | *Scan schema*, open `employees`, tick **Hidden** on `salary_inr` | "Tables and columns are read automatically. Sensitive columns can be hidden: the model never sees them." |
| 4 | 0:55 | **Business terms**: `open PO` = "a purchase order whose status is Open or Partially Received" | "It learns your company's words." |
| 5 | 1:10 | **Documents → Upload** the 20 sample PDFs; wait for *ready*; open the line items of `invoice_KD-2026-077.pdf` | "PDFs become searchable passages, and invoice tables become line items — tax rows are left out." |
| 6 | 1:40 | **Try it**: "Which production orders are delayed and how far behind are they?" | Point at the answer, the rows and the SQL. |
| 7 | 2:00 | Follow-up: "Which buyers are those for?" | "Follow-up questions remember the conversation." |
| 8 | 2:15 | "How many open POs do we have, by supplier?" | "It used our definition of an open PO." |
| 9 | 2:30 | "What does the Northwind manual say about carton marking?" | Point at the citation: file and page 2. |
| 10 | 2:45 | "Which production orders are delayed, and what is the maximum carton weight in the Northwind manual?" | "One question, both sources." |
| 11 | 3:05 | "What is the average salary in production?" | "Hidden data stays hidden — it says it doesn't know." |
| 12 | 3:15 | **API keys → Publishable** for `localhost`, copy the script tag; show a plain HTML page with the widget answering a question | "One script tag puts it on any website; the key only works there." |
| 13 | 3:40 | **Query log**, filter *Marked wrong* | "Every question is logged, and wrong answers feed the test set." |
| 14 | 3:50 | README with the five ways to use it and the evaluation scores | "Open source, MIT licensed: pip install datachat-agent." |

## A plain page for scene 12

Save as `demo.html` next to nothing else and open it with `python -m http.server 5500`
(the key must allow `localhost`):

```html
<!doctype html>
<html><body style="font-family:system-ui;padding:40px">
  <h1>Knitwell Demo ERP</h1>
  <p>Our internal dashboard.</p>
  <script src="http://localhost:8000/widget.js" data-key="dc_pub_..."
          data-title="Ask about our orders"></script>
</body></html>
```

## Screenshots for the README

While recording, also save: the overview, the schema with a hidden column, the documents list,
a cited answer on *Try it*, and the widget on the plain page. Put them in `docs/images/` and add
them to the README.
