import os, sys, unittest, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import ingest, detectors as D

T0 = dt.datetime(2026, 9, 1, 10, tzinfo=dt.timezone.utc)
SINCE, UNTIL = '2026-09-01T00:00:00.000Z', '2026-09-08T00:00:00.000Z'


def ts(sec):
    return (T0 + dt.timedelta(seconds=sec)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


class Builder:
    def __init__(self):
        self.con = ingest.connect(':memory:'); self.n = 0

    def resp(self, sid, aid, atype, model, sec, fresh=10, cw=0, cr=0, out=100, project='P'):
        self.n += 1; mid = f'm{self.n}'
        self.con.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (mid, sid, aid, atype, None, model, ts(sec), fresh, cw, 0, cr, out, None, project))
        return mid

    def tool(self, sid, aid, sec, tool, chars=100, target=None, mid='x'):
        self.n += 1
        self.con.execute('INSERT INTO tool_calls VALUES(?,?,?,?,?,?,?,?,?,?)', (f't{self.n}', mid, sid, aid, ts(sec), tool, 10, chars, 0, target))

    def run(self, aid, parent, atype, sec, prompt, model='claude-opus-5-5'):
        self.con.execute('INSERT INTO subagent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (aid, parent, atype, prompt[:20], prompt, 'done', model, ts(sec), ts(sec + 60), None, 'P'))

    def check(self, fn):
        return fn(D.Window(self.con, SINCE, UNTIL))


class DetectorTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: getattr(D, k) for k in dir(D) if k.isupper()}
        D.RUNAWAY_MIN_RESPONSES = 5; D.RUNAWAY_MIN_AVG_CONTEXT = 1000; D.STARTUP_MIN_SPAWNS = 2; D.STARTUP_MIN_TOKENS = 1000
        D.SKILL_MIN_LOADS = 2; D.SKILL_MIN_TOKENS = 100; D.HEAVY_RESULT_TOKENS = 100; D.FANOUT_SAME_FILE = 2
        D.CACHE_BREAK_MIN_WRITE = 100

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(D, k, v)

    def _typed_runs(self, b, atype, model, tool):
        for i in range(3):
            aid = f'{atype}{i}'
            b.resp('s', aid, atype, model, i * 10, out=100)
            for _ in range(4):
                b.tool('s', aid, i * 10, tool)

    def test_model_mismatch(self):
        b = Builder(); self._typed_runs(b, 'reader', 'claude-opus-5-5', 'Read')
        self.assertEqual([f['subject'] for f in b.check(D.model_mismatch)], ['reader'])
        b = Builder(); self._typed_runs(b, 'writer', 'claude-opus-5-5', 'Edit')
        self.assertEqual(b.check(D.model_mismatch), [])
        b = Builder(); self._typed_runs(b, 'cheap', 'claude-sonnet-5-5', 'Read')
        self.assertEqual(b.check(D.model_mismatch), [])

    def test_runaway_session(self):
        b = Builder()
        for i in range(6):
            b.resp('long', None, 'main', 'claude-opus-5-5', i, cr=500_000)
        for i in range(6):
            b.resp('short', None, 'main', 'claude-opus-5-5', i, cr=10)
        f = b.check(D.runaway_session)
        self.assertEqual([x['subject'] for x in f], ['long']); self.assertGreater(f[0]['saving_dollars'], 0)

    def test_heavy_tool_output_and_rereads(self):
        b = Builder()
        b.tool('s', None, 0, 'Bash', chars=4000, target='cat')
        b.tool('s', None, 0, 'Grep', chars=40)
        for i in range(3):
            b.tool('s', None, i, 'Read', chars=40, target='/a.md')
        for i in range(5):
            b.resp('s', None, 'main', 'claude-opus-5-5', 10 + i)
        subjects = {f['subject'] for f in b.check(D.heavy_tool_output)}
        self.assertEqual(subjects, {'Bash', 'Read (same file re-read)'})

    def test_repeated_work(self):
        b = Builder()
        b.run('a1', 's', 'Explore', 0, 'find the price table in the pricing repo')
        b.run('a2', 's', 'Explore', 100, 'find the price table in the pricing repo')
        b.run('a3', 's', 'Explore', 200, 'summarise the voucher rules')
        f = b.check(D.repeated_work)
        self.assertEqual(len(f), 1); self.assertEqual(f[0]['subject'], 'near-identical subagent re-runs')
        b = Builder()
        b.run('a1', 's', 'Explore', 0, 'find the price table'); b.run('a3', 's', 'Explore', 200, 'summarise voucher rules')
        self.assertEqual(b.check(D.repeated_work), [])

    def test_parallel_siblings_are_not_reruns(self):
        b = Builder()
        b.run('a1', 's', 'Explore', 0, 'find the price table in the pricing repo')
        b.run('a2', 's', 'Explore', 10, 'find the price table in the pricing repo')
        self.assertEqual(b.check(D.repeated_work), [])

    def test_startup_overhead(self):
        b = Builder()
        for i in range(3):
            b.resp('s', f'big{i}', 'bigstart', 'claude-opus-5-5', i, cw=5000)
            b.resp('s', f'small{i}', 'smallstart', 'claude-opus-5-5', i, cw=50)
        self.assertEqual([f['subject'] for f in b.check(D.startup_overhead)], ['bigstart'])

    def test_cache_break(self):
        b = Builder()
        b.resp('s', None, 'main', 'claude-opus-5-5', 0); b.resp('s', None, 'main', 'claude-opus-5-5', 600, cw=5000)
        b.resp('t', None, 'main', 'claude-opus-5-5', 0, project='Q'); b.resp('t', None, 'main', 'claude-opus-5-5', 60, cw=5000, project='Q')
        self.assertEqual([f['subject'] for f in b.check(D.cache_break)], ['P'])

    def test_skill_cost(self):
        b = Builder()
        for i in range(2):
            b.tool('s', None, i * 10, 'Skill:big', chars=4000)
            b.tool('s', None, i * 10, 'Skill:small', chars=4)
        b.resp('s', None, 'main', 'claude-opus-5-5', 50)
        self.assertEqual([f['subject'] for f in b.check(D.skill_cost)], ['Skill:big'])

    def test_performance_flags_rerun_and_correction(self):
        b = Builder()
        b.run('a1', 's', 'Explore', 0, 'find the price table'); b.run('a2', 's', 'Explore', 100, 'find the price table')
        b.run('a3', 's', 'Explore', 1000, 'map voucher code paths')
        b.con.execute("INSERT INTO signals VALUES('s', NULL, ?, 'user_correction', 'no')", (ts(1100),))
        p = D.performance(b.con, SINCE, UNTIL)['Explore']
        self.assertEqual((p['runs'], p['flagged']), (3, 2))


if __name__ == '__main__':
    unittest.main()
