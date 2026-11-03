"""ram's rule checks over ram.db. Each check returns findings; savings are weekly estimates in both measures.

Usage: python3 detectors.py [--db ../ram.db] [--days 7] [--until ISO] [--out findings.json]
"""
import argparse, bisect, collections, datetime as dt, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cost, ingest

# Thresholds. Tune from state/lessons.md.
MISMATCH_READ_SHARE = 0.7        # share of a type's tool calls that only read or search
MISMATCH_MAX_OUT = 600           # average output tokens per response
MISMATCH_MIN_RUNS = 3
RUNAWAY_MIN_RESPONSES = 150
RUNAWAY_MIN_AVG_CONTEXT = 150_000
HEAVY_RESULT_TOKENS = 15_000
REPEAT_READS = 3                 # same file read this many times in one thread
RERUN_SIMILARITY = 0.8
FANOUT_SAME_FILE = 4             # children of one session reading the same file
STARTUP_MIN_TOKENS = 30_000
STARTUP_MIN_SPAWNS = 5
CACHE_TTL_S = 300
CACHE_BREAK_MIN_WRITE = 20_000
SKILL_MIN_TOKENS = 8_000
SKILL_MIN_LOADS = 5
CARRY_CAP = 300                  # a tool result stops being re-read after this many turns (compaction, end)
READ_TOOLS = {'Read', 'Grep', 'Glob', 'Bash', 'WebFetch', 'WebSearch', 'LS', 'ToolSearch'}
CHARS_PER_TOKEN = 4


def _iso(t):
    return dt.datetime.fromisoformat(t.replace('Z', '+00:00'))


class Window:
    """Responses in [since, until) loaded once, with per-thread ordering for carried-cost estimates."""

    def __init__(self, con, since, until):
        self.con, self.since, self.until = con, since, until
        self.days = max((_iso(until) - _iso(since)).total_seconds() / 86400, 1e-9)
        self.scale = 7 / self.days
        cols = 'msg_id, session_id, agent_id, agent_type, skill, model, ts, fresh_in, cache_write_5m, cache_write_1h, cache_read, output, project'
        self.rows = [dict(zip(cols.split(', '), r)) for r in con.execute(
            f'SELECT {cols} FROM responses WHERE ts>=? AND ts<? ORDER BY session_id, agent_id, ts', (since, until))]
        self.threads = collections.defaultdict(list)
        for r in self.rows:
            r['usd'] = cost.dollars(r['model'], r['fresh_in'], r['cache_write_5m'], r['cache_write_1h'], r['cache_read'], r['output'])
            r['oe'] = cost.opus_eq(r['model'], r['fresh_in'], r['cache_write_5m'], r['cache_write_1h'], r['cache_read'], r['output'])
            r['ctx'] = r['fresh_in'] + r['cache_write_5m'] + r['cache_write_1h'] + r['cache_read']
            self.threads[(r['session_id'], r['agent_id'])].append(r)

    def carried(self, session_id, agent_id, ts, tokens):
        """Cost of re-reading `tokens` from cache on every later turn of the thread (both measures)."""
        th = self.threads.get((session_id, agent_id), [])
        i = bisect.bisect_right([r['ts'] for r in th], ts or '')
        later = th[i:i + CARRY_CAP]
        usd = sum(tokens * cost.price_for(r['model'])[0]['cr'] / 1e6 for r in later if r['model'] != '<synthetic>')
        oe = sum(cost.opus_eq(r['model'], 0, 0, 0, tokens, 0) for r in later)
        return usd, oe

    def tools(self, extra='', args=()):
        return self.con.execute(
            'SELECT id, session_id, agent_id, ts, tool, result_chars, is_error, target FROM tool_calls '
            f'WHERE ts>=? AND ts<? {extra}', (self.since, self.until) + tuple(args)).fetchall()


def _finding(w, check, subject, evidence, cost_usd, cost_oe, save_usd, save_oe, basis):
    s = w.scale
    return {'check': check, 'subject': subject, 'evidence': evidence,
            'weekly_dollars': round(cost_usd * s, 2), 'weekly_opus_eq': round(cost_oe * s),
            'saving_dollars': round(save_usd * s, 2), 'saving_opus_eq': round(save_oe * s), 'basis': basis}


def model_mismatch(w):
    out = []
    by_type = collections.defaultdict(list)
    for r in w.rows:
        if r['agent_id'] and r['agent_type'] not in ('main', 'fork'):
            by_type[r['agent_type']].append(r)
    for atype, rows in by_type.items():
        agents = {r['agent_id'] for r in rows}
        top = [r for r in rows if cost.tier(r['model']) in ('opus', 'fable')]
        if len(agents) < MISMATCH_MIN_RUNS or sum(r['oe'] for r in top) < 0.5 * sum(r['oe'] for r in rows):
            continue
        ph = ','.join('?' * len(agents))
        tools = [t for (t,) in w.con.execute(f'SELECT tool FROM tool_calls WHERE agent_id IN ({ph})', list(agents))]
        read_share = sum(t in READ_TOOLS for t in tools) / max(len(tools), 1)
        avg_out = sum(r['output'] for r in rows) / len(rows)
        if read_share < MISMATCH_READ_SHARE or avg_out > MISMATCH_MAX_OUT:
            continue
        usd = sum(r['usd'] for r in top); oe = sum(r['oe'] for r in top)
        son = sum(cost.dollars('claude-sonnet-5-5', r['fresh_in'], r['cache_write_5m'], r['cache_write_1h'], r['cache_read'], r['output']) for r in top)
        son_oe = sum(cost.opus_eq('claude-sonnet-5-5', r['fresh_in'], r['cache_write_5m'], r['cache_write_1h'], r['cache_read'], r['output']) for r in top)
        models = collections.Counter(r['model'] for r in rows)
        out.append(_finding(w, 'model_mismatch', atype,
                            {'runs': len(agents), 'read_share': round(read_share, 2), 'avg_output_tokens': round(avg_out),
                             'models': dict(models.most_common())},
                            usd, oe, usd - son, oe - son_oe,
                            'same tokens re-priced on Sonnet 5.5; assumes no extra turns'))
    return out


def runaway_session(w):
    out = []
    for (sid, aid), th in w.threads.items():
        if aid is not None or len(th) < RUNAWAY_MIN_RESPONSES:
            continue
        avg_ctx = sum(r['ctx'] for r in th) / len(th)
        if avg_ctx < RUNAWAY_MIN_AVG_CONTEXT:
            continue
        title, comp = (w.con.execute('SELECT title, compactions FROM sessions WHERE session_id=?', (sid,)).fetchone() or (None, 0))
        cr_usd = sum(r['cache_read'] * cost.price_for(r['model'])[0]['cr'] / 1e6 for r in th)
        cr_oe = sum(cost.opus_eq(r['model'], 0, 0, 0, r['cache_read'], 0) for r in th)
        out.append(_finding(w, 'runaway_session', sid,
                            {'project': th[0]['project'], 'title': title, 'responses': len(th),
                             'avg_context_tokens': round(avg_ctx), 'compactions': comp},
                            sum(r['usd'] for r in th), sum(r['oe'] for r in th), cr_usd * 0.4, cr_oe * 0.4,
                            'splitting into sessions of half the length cuts re-read context by about 40%'))
    return out


def heavy_tool_output(w):
    out = []
    agg = collections.defaultdict(lambda: {'calls': 0, 'tokens': 0, 'usd': 0.0, 'oe': 0.0, 'examples': []})
    for tid, sid, aid, ts, tool, chars, err, target in w.tools('AND result_chars>=?', [HEAVY_RESULT_TOKENS * CHARS_PER_TOKEN]):
        tok = chars / CHARS_PER_TOKEN
        usd, oe = w.carried(sid, aid, ts, tok)
        a = agg[tool]; a['calls'] += 1; a['tokens'] += tok; a['usd'] += usd; a['oe'] += oe
        if len(a['examples']) < 5:
            a['examples'].append({'session': sid, 'target': target, 'tokens': round(tok)})
    for tool, a in agg.items():
        out.append(_finding(w, 'heavy_tool_output', tool, {'calls': a['calls'], 'result_tokens': round(a['tokens']), 'examples': a['examples']},
                            a['usd'], a['oe'], a['usd'] * 0.5, a['oe'] * 0.5,
                            'cost of re-reading each large result on later turns; half is avoidable with ranges or filters'))
    reads = collections.defaultdict(list)
    for tid, sid, aid, ts, tool, chars, err, target in w.tools("AND tool='Read'"):
        reads[(sid, aid, target)].append((ts, chars))
    usd = oe = 0.0; n = 0; ex = []
    for (sid, aid, target), rs in reads.items():
        if len(rs) < REPEAT_READS:
            continue
        n += 1
        for ts, chars in sorted(rs)[1:]:
            u, o = w.carried(sid, aid, ts, chars / CHARS_PER_TOKEN); usd += u; oe += o
        if len(ex) < 5:
            ex.append({'session': sid, 'file': target, 'reads': len(rs)})
    if n:
        out.append(_finding(w, 'heavy_tool_output', 'Read (same file re-read)', {'threads': n, 'examples': ex},
                            usd, oe, usd, oe, 'every read after the first is avoidable'))
    return out


def _words(s):
    return set(re.findall(r'\w+', (s or '').lower()))


def _sim(a, b):
    return len(a & b) / max(len(a | b), 1) if a and b else 0.0


def find_reruns(runs):
    """runs: iterable of (agent_id, parent, agent_type, description, prompt, started, ended), in start order.
    A re-run repeats an earlier run of the same type in the same session that had already finished: same short
    description and nearly the same task ending. Parallel siblings (fan-out) and forks are not re-runs."""
    done = collections.defaultdict(list)
    reruns = set()
    for aid, parent, atype, desc, prompt, started, ended in runs:
        d, p = _words(desc), _words((prompt or '')[-4000:])
        if atype != 'fork' and any(e and started and e < started and _sim(d, pd) >= RERUN_SIMILARITY and _sim(p, pp) >= 0.9
                                   for pd, pp, e in done[(parent, atype)]):
            reruns.add(aid)
        done[(parent, atype)].append((d, p, ended))
    return reruns


def repeated_work(w):
    out = []
    runs = w.con.execute('SELECT agent_id, parent_session, agent_type, description, task_prompt, started, ended FROM subagent_runs '
                         'WHERE started>=? AND started<? ORDER BY started', (w.since, w.until)).fetchall()
    cost_by_agent = collections.defaultdict(lambda: [0.0, 0.0])
    for r in w.rows:
        if r['agent_id']:
            c = cost_by_agent[r['agent_id']]; c[0] += r['usd']; c[1] += r['oe']
    reruns = find_reruns(runs)
    usd = sum(cost_by_agent[a][0] for a in reruns); oe = sum(cost_by_agent[a][1] for a in reruns)
    ex = [{'session': r[1], 'agent_type': r[2], 'description': r[3]} for r in runs if r[0] in reruns][:5]
    if ex:
        out.append(_finding(w, 'repeated_work', 'near-identical subagent re-runs', {'examples': ex}, usd, oe, usd * 0.5, oe * 0.5,
                            'half of re-runs assumed avoidable (some are deliberate retries)'))
    fan = collections.defaultdict(set)
    rows = w.con.execute("SELECT t.session_id, t.target, t.agent_id, t.result_chars, t.ts FROM tool_calls t "
                         "WHERE t.tool='Read' AND t.agent_id IS NOT NULL AND t.ts>=? AND t.ts<?", (w.since, w.until)).fetchall()
    info = {}
    for sid, target, aid, chars, ts in rows:
        fan[(sid, target)].add(aid); info.setdefault((sid, target), []).append((aid, ts, chars))
    usd = oe = 0.0; ex = []
    for key, agents in fan.items():
        if len(agents) < FANOUT_SAME_FILE:
            continue
        for aid, ts, chars in info[key][1:]:
            u, o = w.carried(key[0], aid, ts, chars / CHARS_PER_TOKEN); usd += u; oe += o
        if len(ex) < 5:
            ex.append({'session': key[0], 'file': key[1], 'children': len(agents)})
    if ex:
        out.append(_finding(w, 'repeated_work', 'fan-out children re-reading the same file', {'examples': ex}, usd, oe, usd * 0.7, oe * 0.7,
                            'pass the needed excerpt in the brief instead of each child reading the file'))
    return out


def startup_overhead(w):
    out = []
    by_type = collections.defaultdict(list)
    for (sid, aid), th in w.threads.items():
        by_type[th[0]['agent_type'] if aid else 'main (session start)'].append(th[0])
    for atype, firsts in by_type.items():
        avg = sum(r['ctx'] for r in firsts) / len(firsts)
        if len(firsts) < STARTUP_MIN_SPAWNS or avg < STARTUP_MIN_TOKENS:
            continue
        usd = sum(r['usd'] for r in firsts); oe = sum(r['oe'] for r in firsts)
        out.append(_finding(w, 'startup_overhead', atype, {'spawns': len(firsts), 'avg_first_turn_context': round(avg)},
                            usd, oe, usd * 0.3, oe * 0.3,
                            'first-turn cost only; trimming preloaded skills, CLAUDE.md and MCP tool lists by ~30% (later turns re-read it too, so this is a floor)'))
    return out


def cache_break(w):
    agg = collections.defaultdict(lambda: [0, 0.0, 0.0, None])
    for (sid, aid), th in w.threads.items():
        for a, b in zip(th, th[1:]):
            cw = b['cache_write_5m'] + b['cache_write_1h']
            if cw < CACHE_BREAK_MIN_WRITE or (_iso(b['ts']) - _iso(a['ts'])).total_seconds() <= CACHE_TTL_S:
                continue
            p = cost.price_for(b['model'])[0]
            x = agg[b['project']]
            x[0] += 1
            x[1] += cw * (p['in'] * 1.25 - p['cr']) / 1e6
            x[2] += cost.opus_eq(b['model'], 0, cw, 0, 0, 0) - cost.opus_eq(b['model'], 0, 0, 0, cw, 0)
            x[3] = x[3] or sid
    return [_finding(w, 'cache_break', proj, {'breaks': n, 'example_session': ex}, usd, oe, usd * 0.5, oe * 0.5,
                     'extra cost of rewriting cache after an idle gap over 5 minutes; half avoidable by clearing or resuming sooner')
            for proj, (n, usd, oe, ex) in agg.items()]


def skill_cost(w):
    out = []
    agg = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
    for tid, sid, aid, ts, tool, chars, err, target in w.tools("AND tool LIKE 'Skill:%'"):
        # the skill body arrives in the next user turn, not always in the tool result; use the larger of the two
        tok = chars / CHARS_PER_TOKEN
        nxt = w.con.execute("SELECT cache_write_5m + cache_write_1h FROM responses WHERE session_id=? AND agent_id IS ? AND ts>? ORDER BY ts LIMIT 1",
                            (sid, aid, ts)).fetchone()
        tok = max(tok, (nxt[0] if nxt else 0) * 0.8)
        u, o = w.carried(sid, aid, ts, tok)
        a = agg[tool]; a[0] += 1; a[1] += tok; a[2] += u; a[3] += o
    for tool, (n, tok, usd, oe) in agg.items():
        if n >= SKILL_MIN_LOADS and tok / n >= SKILL_MIN_TOKENS:
            out.append(_finding(w, 'skill_cost', tool, {'loads': n, 'avg_tokens_per_load': round(tok / n)}, usd, oe, usd * 0.4, oe * 0.4,
                                'moving reference sections to on-demand files typically cuts the loaded text by ~40%'))
    return out


CHECKS = [model_mismatch, runaway_session, heavy_tool_output, repeated_work, startup_overhead, cache_break, skill_cost]


def run_all(con, since, until):
    w = Window(con, since, until)
    found = [f for c in CHECKS for f in c(w)]
    return sorted(found, key=lambda f: -f['saving_opus_eq'])


def totals(con, since, until):
    """Totals by agent type, model, project and skill in both measures, plus data-quality notes."""
    w = Window(con, since, until)
    def group(key):
        g = collections.defaultdict(lambda: {'tokens': 0, 'dollars': 0.0, 'opus_eq': 0.0, 'responses': 0})
        for r in w.rows:
            x = g[key(r) or '-']; x['tokens'] += r['ctx'] + r['output']; x['dollars'] += r['usd']; x['opus_eq'] += r['oe']; x['responses'] += 1
        return dict(sorted(((k, {kk: round(vv, 2) for kk, vv in v.items()}) for k, v in g.items()), key=lambda kv: -kv[1]['opus_eq']))
    unknown = sorted({r['model'] for r in w.rows if r['model'] != '<synthetic>' and not cost.price_for(r['model'])[1]})
    skipped, lines = con.execute('SELECT SUM(skipped_lines), SUM(lines_read) FROM files').fetchone()
    return {'since': since, 'until': until,
            'all': {'tokens': sum(r['ctx'] + r['output'] for r in w.rows), 'dollars': round(sum(r['usd'] for r in w.rows), 2),
                    'opus_eq': round(sum(r['oe'] for r in w.rows)), 'responses': len(w.rows),
                    'sessions': len({r['session_id'] for r in w.rows})},
            'by_agent_type': group(lambda r: r['agent_type']), 'by_model': group(lambda r: r['model']),
            'by_project': group(lambda r: r['project']), 'by_skill': group(lambda r: r['skill']),
            'unknown_models': unknown, 'skipped_line_share': round((skipped or 0) / max(lines or 1, 1), 4)}


def performance(con, since, until):
    """Per agent type: runs, flagged runs (re-run, or followed by a correction/interrupt within 10 minutes), cost per good run."""
    w = Window(con, since, until)
    cost_by_agent = collections.defaultdict(lambda: [0.0, 0.0])
    for r in w.rows:
        if r['agent_id']:
            c = cost_by_agent[r['agent_id']]; c[0] += r['usd']; c[1] += r['oe']
    sig = collections.defaultdict(list)
    for sid, ts, kind in con.execute("SELECT session_id, ts, kind FROM signals WHERE kind IN ('user_correction','user_interrupt') AND ts>=? AND ts<?", (since, until)):
        sig[sid].append(ts)
    per = collections.defaultdict(lambda: {'runs': 0, 'flagged': 0, 'dollars': 0.0, 'opus_eq': 0.0, 'models': collections.Counter()})
    runs = con.execute('SELECT agent_id, parent_session, agent_type, description, task_prompt, started, ended, model FROM subagent_runs '
                       'WHERE started>=? AND started<? ORDER BY started', (since, until)).fetchall()
    reruns = find_reruns([r[:7] for r in runs])
    for aid, parent, atype, desc, prompt, started, ended, model in runs:
        p = per[atype]; p['runs'] += 1; p['models'][model] += 1
        p['dollars'] += cost_by_agent[aid][0]; p['opus_eq'] += cost_by_agent[aid][1]
        corrected = ended and any(ended <= t <= (_iso(ended) + dt.timedelta(minutes=10)).isoformat().replace('+00:00', 'Z') for t in sig.get(parent, []))
        if aid in reruns or corrected:
            p['flagged'] += 1
    out = {}
    for atype, p in sorted(per.items(), key=lambda kv: -kv[1]['opus_eq']):
        good = p['runs'] - p['flagged']
        out[atype] = {'runs': p['runs'], 'flagged': p['flagged'], 'success_rate': round(good / p['runs'], 3) if p['runs'] else None,
                      'dollars': round(p['dollars'], 2), 'opus_eq': round(p['opus_eq']),
                      'dollars_per_good_run': round(p['dollars'] / good, 3) if good else None,
                      'opus_eq_per_good_run': round(p['opus_eq'] / good) if good else None,
                      'models': dict(p['models'].most_common())}
    return out


def window(days, until=None):
    u = _iso(until) if until else dt.datetime.now(dt.timezone.utc)
    f = lambda t: t.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    return f(u - dt.timedelta(days=days)), f(u)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=ingest.DEFAULT_DB)
    ap.add_argument('--days', type=float, default=7)
    ap.add_argument('--until')
    ap.add_argument('--out')
    a = ap.parse_args()
    con = ingest.connect(a.db)
    since, until = window(a.days, a.until)
    res = {'totals': totals(con, since, until), 'findings': run_all(con, since, until), 'performance': performance(con, since, until)}
    s = json.dumps(res, indent=1, ensure_ascii=False)
    if a.out:
        open(a.out, 'w').write(s); print(a.out)
    else:
        print(s)
