"""Read-only SQL over ram.db for the ram agent. Usage: python3 q.py "SELECT ..." [--limit 200]"""
import argparse, os, re, sqlite3, sys

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ram.db')


def run(sql, limit=200, db=DB):
    if not re.match(r'^\s*(select|with)\b', sql, re.I) or ';' in sql.strip().rstrip(';'):
        raise SystemExit('q.py runs one SELECT or WITH statement only')
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    return cols, cur.fetchmany(limit)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('sql'); ap.add_argument('--limit', type=int, default=200)
    a = ap.parse_args()
    cols, rows = run(a.sql, a.limit)
    print('\t'.join(cols))
    for r in rows:
        print('\t'.join('' if v is None else str(v) for v in r))
