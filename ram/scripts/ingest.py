"""Load Claude Code transcripts (~/.claude/projects/**/*.jsonl) into ram.db, incrementally.

Usage: python3 ingest.py [--root ~/.claude/projects] [--db ../ram.db]
"""
import argparse, glob, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, '..', 'ram.db')
DEFAULT_ROOT = os.path.expanduser('~/.claude/projects')

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, mtime REAL, size INTEGER, offset INTEGER,
  lines_read INTEGER, skipped_lines INTEGER);
CREATE TABLE IF NOT EXISTS sessions(session_id TEXT PRIMARY KEY, project TEXT, title TEXT, first_prompt TEXT,
  started TEXT, ended TEXT, version TEXT, turns INTEGER DEFAULT 0, compactions INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS responses(msg_id TEXT PRIMARY KEY, session_id TEXT, agent_id TEXT, agent_type TEXT,
  skill TEXT, model TEXT, ts TEXT, fresh_in INTEGER, cache_write_5m INTEGER, cache_write_1h INTEGER,
  cache_read INTEGER, output INTEGER, effort TEXT, project TEXT);
CREATE TABLE IF NOT EXISTS tool_calls(id TEXT PRIMARY KEY, msg_id TEXT, session_id TEXT, agent_id TEXT, ts TEXT,
  tool TEXT, input_chars INTEGER, result_chars INTEGER DEFAULT 0, is_error INTEGER DEFAULT 0, target TEXT);
CREATE TABLE IF NOT EXISTS subagent_runs(agent_id TEXT PRIMARY KEY, parent_session TEXT, agent_type TEXT,
  description TEXT, task_prompt TEXT, final_output TEXT, model TEXT, started TEXT, ended TEXT,
  spawn_tool_use_id TEXT, project TEXT);
CREATE TABLE IF NOT EXISTS signals(session_id TEXT, agent_id TEXT, ts TEXT, kind TEXT, detail TEXT,
  UNIQUE(session_id, agent_id, ts, kind));
CREATE INDEX IF NOT EXISTS r_ts ON responses(ts);
CREATE INDEX IF NOT EXISTS r_sess ON responses(session_id);
CREATE INDEX IF NOT EXISTS t_sess ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS s_sess ON signals(session_id);
"""

# A typed user message that corrects or pushes back on the previous answer.
CORRECTION = re.compile(
    r"^\s*(no\b|nope|wrong|that'?s (not|wrong)|not (what|right)|actually\b|i (said|meant|asked)|stop\b|"
    r"you (missed|forgot|didn'?t)|why did you|don'?t\b|نه\b|اشتباه|غلط|نخیر)", re.I)


def connect(db_path=DEFAULT_DB):
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    return con


def project_name(top):
    """Claude Code names a project folder after its path: -Users-<name>-Projects-<project>. Show just the project."""
    n = top
    m = re.match(r'-Users-[^-]+-(?:Projects|Documents|code)-(.+)', n)
    if m:
        return m.group(1)
    if n.startswith('-private-tmp'):
        return '(scratch) tmp'
    return re.sub(r'^-Users-[^-]+', '~', n)

def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text')
    return ''


def _target(name, inp):
    if name in ('Read', 'Edit', 'Write', 'NotebookEdit'):
        return inp.get('file_path')
    if name == 'Bash':
        cmd = str(inp.get('command', '')).strip()
        cmd = re.sub(r'^(cd\s+("[^"]*"|\S+)\s*&&\s*)', '', cmd)
        return (cmd.split() or [''])[0][:60]
    if name in ('Grep', 'Glob'):
        return str(inp.get('pattern'))[:80]
    return None


def _tool_name(b):
    name, inp = b.get('name'), b.get('input') or {}
    if name == 'Skill':
        return 'Skill:' + str(inp.get('skill'))
    if name in ('Agent', 'Task'):
        return 'Agent:' + str(inp.get('subagent_type') or 'general-purpose')
    return name


def ingest_file(con, path, root):
    st = os.stat(path)
    row = con.execute('SELECT mtime, size, offset, lines_read, skipped_lines FROM files WHERE path=?', (path,)).fetchone()
    if row and row[0] == st.st_mtime and row[1] == st.st_size:
        return 0, 0
    offset, lines_read, skipped = (row[2], row[3], row[4]) if row and st.st_size >= row[1] else (0, 0, 0)

    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    project = project_name(parts[0])
    is_sub = 'subagents' in parts
    meta = {}
    if is_sub:
        mp = path[:-6] + '.meta.json'
        if os.path.exists(mp):
            try:
                with open(mp) as f:
                    meta = json.load(f)
            except ValueError:
                pass
    parent_session = parts[1] if is_sub and len(parts) > 2 else None
    agent_id = os.path.basename(path)[6:-6] if is_sub else None  # agent-<id>.jsonl
    agent_type = meta.get('agentType') or ('subagent' if is_sub else 'main')
    new = 0

    with open(path, 'rb') as fh:
        fh.seek(offset)
        for raw in fh:
            if not raw.endswith(b'\n'):
                break  # partial last line; read it next time
            offset += len(raw)
            lines_read += 1
            try:
                d = json.loads(raw)
            except ValueError:
                skipped += 1
                continue
            new += _line(con, d, project, is_sub, parent_session, agent_id, agent_type, meta)

    con.execute('INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?)',
                (path, st.st_mtime, st.st_size, offset, lines_read, skipped))
    return new, skipped


def _line(con, d, project, is_sub, parent_session, agent_id, agent_type, meta):
    t = d.get('type')
    sid = parent_session or d.get('sessionId')
    ts = d.get('timestamp')
    if not sid:
        return 0
    if not is_sub:
        con.execute('INSERT OR IGNORE INTO sessions(session_id, project) VALUES(?,?)', (sid, project))
        if ts:
            con.execute('UPDATE sessions SET started=COALESCE(MIN(started, ?), ?), ended=MAX(COALESCE(ended, ?), ?),'
                        ' version=COALESCE(?, version) WHERE session_id=?', (ts, ts, ts, ts, d.get('version'), sid))
        title = d.get('customTitle') or d.get('aiTitle')
        if title:
            # a title the user set wins over an AI one
            con.execute('UPDATE sessions SET title=? WHERE session_id=? AND (? OR title IS NULL)',
                        (title, sid, 1 if d.get('customTitle') else 0))
    else:
        con.execute('INSERT OR IGNORE INTO subagent_runs(agent_id, parent_session, agent_type, description,'
                    ' spawn_tool_use_id, project, started) VALUES(?,?,?,?,?,?,?)',
                    (agent_id, parent_session, agent_type, meta.get('description'), meta.get('toolUseId'), project, ts))
        if ts:
            con.execute('UPDATE subagent_runs SET ended=? WHERE agent_id=?', (ts, agent_id))

    if t == 'system':
        sub = d.get('subtype')
        if sub == 'compact_boundary':
            _signal(con, sid, agent_id, ts, 'compaction', json.dumps(d.get('compactMetadata')))
            if not is_sub:
                con.execute('UPDATE sessions SET compactions=compactions+1 WHERE session_id=?', (sid,))
        elif sub == 'api_error':
            _signal(con, sid, agent_id, ts, 'api_error', None)
        elif sub == 'turn_duration':
            _signal(con, sid, agent_id, ts, 'turn_duration', str(d.get('durationMs')))
        return 0

    if t == 'user':
        msg = d.get('message') or {}
        c = msg.get('content')
        if isinstance(c, list):
            for b in c:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    rc = b.get('content')
                    size = len(rc) if isinstance(rc, str) else len(json.dumps(rc))
                    err = 1 if b.get('is_error') else 0
                    con.execute('UPDATE tool_calls SET result_chars=?, is_error=? WHERE id=?',
                                (size, err, b.get('tool_use_id')))
                    if err:
                        txt = _text(rc) if not isinstance(rc, str) else rc
                        kind = 'rejected_tool' if ("doesn't want to proceed" in txt or 'Permission for this action' in txt) else 'tool_error'
                        _signal(con, sid, agent_id, ts, kind, b.get('tool_use_id'))
        text = _text(c)
        if is_sub:
            con.execute('UPDATE subagent_runs SET task_prompt=COALESCE(task_prompt, ?) WHERE agent_id=?',
                        (text[:20000] or None, agent_id))
            return 0
        if '[Request interrupted by user' in text:
            _signal(con, sid, agent_id, ts, 'user_interrupt', None)
            return 0
        typed = isinstance(c, str) or any(isinstance(b, dict) and b.get('type') == 'text' for b in (c or []))
        if typed and text and not text.lstrip().startswith('<') and not d.get('isMeta') and not d.get('isCompactSummary'):
            con.execute('UPDATE sessions SET turns=turns+1, first_prompt=COALESCE(first_prompt, ?) WHERE session_id=?',
                        (text[:300], sid))
            if CORRECTION.search(text):
                _signal(con, sid, agent_id, ts, 'user_correction', text[:200])
        return 0

    if t != 'assistant':
        return 0
    m = d.get('message') or {}
    u = m.get('usage') or {}
    cc = u.get('cache_creation') or {}
    cw = u.get('cache_creation_input_tokens', 0) or 0
    cw1h = cc.get('ephemeral_1h_input_tokens', 0) or 0
    cw5m = cc.get('ephemeral_5m_input_tokens', cw - cw1h) or 0
    key = m.get('id') or d.get('requestId') or d.get('uuid')
    atype = agent_type if is_sub else (d.get('attributionAgent') or 'main')
    cur = con.execute(
        'INSERT INTO responses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(msg_id) DO UPDATE SET '
        'fresh_in=excluded.fresh_in, cache_write_5m=excluded.cache_write_5m, cache_write_1h=excluded.cache_write_1h,'
        ' cache_read=excluded.cache_read, output=excluded.output',
        (key, sid, agent_id, atype, d.get('attributionSkill'), m.get('model'), ts, u.get('input_tokens', 0) or 0,
         cw5m, cw1h, u.get('cache_read_input_tokens', 0) or 0, u.get('output_tokens', 0) or 0,
         d.get('effort'), project))
    for b in m.get('content') or []:
        if not isinstance(b, dict):
            continue
        if b.get('type') == 'tool_use':
            inp = b.get('input') or {}
            con.execute('INSERT OR IGNORE INTO tool_calls(id, msg_id, session_id, agent_id, ts, tool, input_chars,'
                        ' target) VALUES(?,?,?,?,?,?,?,?)',
                        (b.get('id'), key, sid, agent_id, ts, _tool_name(b), len(json.dumps(inp)),
                         _target(b.get('name'), inp)))
        elif b.get('type') == 'text' and is_sub and b.get('text'):
            con.execute('UPDATE subagent_runs SET final_output=?, model=? WHERE agent_id=?',
                        (b['text'][:20000], m.get('model'), agent_id))
    return 1


def _signal(con, sid, agent_id, ts, kind, detail):
    con.execute('INSERT OR IGNORE INTO signals VALUES(?,?,?,?,?)', (sid, agent_id, ts, kind, detail))


def ingest(root=DEFAULT_ROOT, db_path=DEFAULT_DB):
    con = connect(db_path)
    new = skipped = files = 0
    for path in sorted(glob.glob(os.path.join(root, '**', '*.jsonl'), recursive=True)):
        n, s = ingest_file(con, path, root)
        new += n; skipped += s; files += 1
    con.commit()
    total = con.execute('SELECT COUNT(*) FROM responses').fetchone()[0]
    return {'files': files, 'new_response_lines': new, 'skipped_lines': skipped, 'responses_total': total}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--db', default=DEFAULT_DB)
    a = ap.parse_args()
    print(json.dumps(ingest(a.root, a.db)))
