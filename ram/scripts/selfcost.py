"""ram's own cost since a time: responses recorded in the ram project folder (lead and judges).

Usage: python3 selfcost.py --since ISO [--report report.md]   (appends a line to the report when given)
"""
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cost, ingest

ap = argparse.ArgumentParser(); ap.add_argument('--since', required=True); ap.add_argument('--report'); ap.add_argument('--heading', action='store_true')
a = ap.parse_args()
con = ingest.connect()
rows = con.execute("SELECT model, fresh_in, cache_write_5m, cache_write_1h, cache_read, output FROM responses "
                   "WHERE project='agent-stack-ram' AND ts>=?", (a.since,)).fetchall()
usd = sum(cost.dollars(*r) for r in rows); oe = sum(cost.opus_eq(*r) for r in rows)
line = f'ram\'s own cost for this run, measured after it finished: ${usd:.2f} / {oe / 1e6:.1f}M Opus-equivalent tokens over {len(rows)} responses.'
print(line)
if a.report and os.path.exists(a.report):
    with open(a.report, 'a') as f:
        f.write(('\n## Cost of this review\n\n' if a.heading else '\n') + line + '\n')
