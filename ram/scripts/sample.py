"""Pick subagent runs for ram-judge and trim each to what a grader needs.

Usage: python3 sample.py --days 7 --n 15 --out <run>/samples [--types a,b]
Weighted toward the costliest agent types and model_mismatch candidates, at least two runs per chosen type.
"""
import argparse, collections, json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cost, detectors, ingest

MAX_OUTPUT_CHARS = 6000
MAX_PROMPT_CHARS = 4000


def pick(con, since, until, n, types=None, seed=0):
    w = detectors.Window(con, since, until)
    by_agent = collections.defaultdict(lambda: [0.0, 0.0, None, None])
    for r in w.rows:
        if r['agent_id']:
            a = by_agent[r['agent_id']]; a[0] += r['usd']; a[1] += r['oe']; a[2] = r['agent_type']; a[3] = r['model']
    type_cost = collections.Counter()
    for aid, (usd, oe, atype, model) in by_agent.items():
        type_cost[atype] += oe
    mism = {f['subject'] for f in detectors.model_mismatch(w)}
    chosen_types = types or [t for t, _ in type_cost.most_common() if t not in ('fork',)][:max(n // 2, 1)]
    chosen_types = sorted(set(chosen_types) | (mism if not types else set()), key=lambda t: -type_cost[t])
    rng = random.Random(seed)
    per_type = max(2, n // max(len(chosen_types), 1))
    picked = []
    for t in chosen_types:
        ids = [a for a, v in by_agent.items() if v[2] == t]
        rng.shuffle(ids)
        picked += [(a, by_agent[a]) for a in ids[:per_type]]
    return picked[:max(n, 2 * len(mism))]


def trim(con, agent_id, usage):
    run = con.execute('SELECT parent_session, agent_type, description, task_prompt, final_output, model, started, ended '
                      'FROM subagent_runs WHERE agent_id=?', (agent_id,)).fetchone()
    if not run:
        return None
    parent, atype, desc, prompt, final, model, started, ended = run
    tools = [{'tool': t, 'target': tg, 'result_tokens': round(c / 4), 'error': bool(e)} for t, tg, c, e in con.execute(
        'SELECT tool, target, result_chars, is_error FROM tool_calls WHERE agent_id=? ORDER BY ts', (agent_id,))]
    follow = [{'kind': k, 'detail': d} for k, d in con.execute(
        "SELECT kind, detail FROM signals WHERE session_id=? AND agent_id IS NULL AND ts>=? AND kind IN "
        "('user_correction','user_interrupt') ORDER BY ts LIMIT 3", (parent, ended or ''))]
    p = prompt or ''
    return {'agent_id': agent_id, 'agent_type': atype, 'model': model, 'description': desc,
            'task_prompt': p if len(p) <= MAX_PROMPT_CHARS else p[:1500] + '\n[...]\n' + p[-2500:],
            'final_output': (final or '')[:MAX_OUTPUT_CHARS], 'tools': tools[:80], 'tool_count': len(tools),
            'followup_signals_in_parent': follow, 'dollars': round(usage[0], 3), 'opus_eq': round(usage[1]),
            'started': started, 'ended': ended, 'parent_session': parent}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=ingest.DEFAULT_DB); ap.add_argument('--days', type=float, default=7)
    ap.add_argument('--until'); ap.add_argument('--n', type=int, default=15); ap.add_argument('--out', required=True)
    ap.add_argument('--types'); ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args()
    con = ingest.connect(a.db)
    since, until = detectors.window(a.days, a.until)
    os.makedirs(a.out, exist_ok=True)
    written = []
    for aid, usage in pick(con, since, until, a.n, a.types.split(',') if a.types else None, a.seed):
        s = trim(con, aid, usage)
        if s:
            path = os.path.join(a.out, f'{aid}.json')
            json.dump(s, open(path, 'w'), ensure_ascii=False, indent=1)
            written.append((s['agent_type'], s['model'], path))
    for t, m, p in written:
        print(f'{t}\t{m}\t{p}')
