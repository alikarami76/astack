"""Proposals and applied changes for ram, with before/after measurement.

  changes.py propose <proposal.json>        append a pending proposal (prints its id)
  changes.py list [--status pending]        show proposals
  changes.py approve <id> | reject <id> [--why "..."]
  changes.py applied <id> --files a b ...   record the edit; baseline was taken at approve
  changes.py evaluate                       verdict for applied changes with >= 7 days of data after

A proposal's `scope` says what to measure: {"agent_type": "X"}, {"project": "P"} or {"all": true}.
"""
import argparse, datetime as dt, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import detectors, ingest

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'state')
BASELINE_DAYS = 14
MIN_DAYS_AFTER = 7
MIN_RUNS = 3
MAX_SUCCESS_DROP = 0.10


def _path(name, state=STATE):
    return os.path.join(state, name)


def _load(name, state=STATE):
    p = _path(name, state)
    return [json.loads(l) for l in open(p)] if os.path.exists(p) else []


def _save(name, rows, state=STATE):
    os.makedirs(state, exist_ok=True)
    with open(_path(name, state), 'w') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def measure(con, scope, since, until):
    """Cost per day (both measures), runs, cost per run and success rate for a scope over a window."""
    days = max((detectors._iso(until) - detectors._iso(since)).total_seconds() / 86400, 1e-9)
    if 'agent_type' in scope:
        p = detectors.performance(con, since, until).get(scope['agent_type'])
        if not p:
            return {'days': days, 'runs': 0}
        return {'days': round(days, 2), 'runs': p['runs'], 'success_rate': p['success_rate'],
                'dollars_per_run': round(p['dollars'] / p['runs'], 4), 'opus_eq_per_run': round(p['opus_eq'] / p['runs']),
                'dollars_per_day': round(p['dollars'] / days, 2), 'opus_eq_per_day': round(p['opus_eq'] / days)}
    t = detectors.totals(con, since, until)
    g = t['by_project'].get(scope['project'], {}) if 'project' in scope else t['all']
    return {'days': round(days, 2), 'runs': g.get('responses', 0),
            'dollars_per_day': round(g.get('dollars', 0) / days, 2), 'opus_eq_per_day': round(g.get('opus_eq', 0) / days)}


def propose(p, state=STATE):
    rows = _load('proposals.jsonl', state)
    slug = re.sub(r'[^a-z0-9]+', '-', str(p.get('subject', '')).lower()).strip('-')[:40]
    base = f"{_now()[:10]}-{p.get('check', 'manual')}-{slug}"
    pid, i = base, 2
    while any(r['id'] == pid for r in rows):
        pid, i = f'{base}-{i}', i + 1
    p = dict(p, id=pid, created=_now(), status='pending')
    p.setdefault('scope', {'all': True})
    rows.append(p); _save('proposals.jsonl', rows, state)
    return pid


def _set(pid, state=STATE, **kw):
    rows = _load('proposals.jsonl', state)
    for r in rows:
        if r['id'] == pid:
            r.update(kw); _save('proposals.jsonl', rows, state); return r
    raise SystemExit(f'no proposal {pid}')


def approve(con, pid, state=STATE, now=None):
    now = now or _now()
    p = _set(pid, state, status='approved', decided=now)
    since = (detectors._iso(now) - dt.timedelta(days=BASELINE_DAYS)).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    ch = _load('changes.jsonl', state)
    ch.append({'proposal_id': pid, 'scope': p['scope'], 'approved': now, 'applied': None, 'files_changed': [],
               'baseline': dict(measure(con, p['scope'], since, now), window=[since, now]), 'verdict': 'pending'})
    _save('changes.jsonl', ch, state)
    return ch[-1]


def applied(pid, files, state=STATE, now=None):
    _set(pid, state, status='applied')
    ch = _load('changes.jsonl', state)
    for c in ch:
        if c['proposal_id'] == pid:
            c['applied'] = now or _now(); c['files_changed'] = files
    _save('changes.jsonl', ch, state)


def verdict(base, after):
    if after.get('days', 0) < MIN_DAYS_AFTER or after.get('runs', 0) < MIN_RUNS or base.get('runs', 0) < MIN_RUNS:
        return 'not_enough_data', 'fewer than 7 days or 3 runs on one side'
    key = 'opus_eq_per_run' if 'opus_eq_per_run' in base else 'opus_eq_per_day'
    cheaper = after[key] < base[key]
    drop = (base.get('success_rate') or 0) - (after.get('success_rate') or 0) if 'success_rate' in base else 0
    if drop > MAX_SUCCESS_DROP:
        return 'revert', f'success rate fell {drop:.0%}'
    if cheaper:
        return 'keep', f'{key} {base[key]} -> {after[key]}; success drop {drop:.0%}'
    return 'revert', f'{key} did not fall ({base[key]} -> {after[key]})'


def evaluate(con, state=STATE, now=None):
    now = now or _now()
    ch = _load('changes.jsonl', state)
    for c in ch:
        if not c.get('applied') or c['verdict'] in ('keep', 'revert'):
            continue
        after = measure(con, c['scope'], c['applied'], now)
        c['verdict'], c['reason'] = verdict(c['baseline'], after)
        c['after'] = after; c['evaluated'] = now
    _save('changes.jsonl', ch, state)
    return ch


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['propose', 'list', 'approve', 'reject', 'applied', 'evaluate'])
    ap.add_argument('arg', nargs='?')
    ap.add_argument('--status'); ap.add_argument('--files', nargs='*', default=[]); ap.add_argument('--why')
    ap.add_argument('--db', default=ingest.DEFAULT_DB)
    a = ap.parse_args()
    con = ingest.connect(a.db)
    if a.cmd == 'propose':
        print(propose(json.load(open(a.arg))))
    elif a.cmd == 'list':
        for r in _load('proposals.jsonl'):
            if not a.status or r['status'] == a.status:
                print(json.dumps(r, ensure_ascii=False))
    elif a.cmd == 'approve':
        print(json.dumps(approve(con, a.arg), ensure_ascii=False))
    elif a.cmd == 'reject':
        _set(a.arg, status='rejected', why=a.why, decided=_now()); print('rejected', a.arg)
    elif a.cmd == 'applied':
        applied(a.arg, a.files); print('applied', a.arg)
    elif a.cmd == 'evaluate':
        for c in evaluate(con):
            print(c['proposal_id'], c['verdict'], c.get('reason', ''))
