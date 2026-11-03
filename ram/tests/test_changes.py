import os, sys, tempfile, shutil, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import changes, q
from test_detectors import Builder


def ts(day, h=10):
    return f'2026-09-{day:02d}T{h:02d}:00:00.000Z'


class ChangesTest(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(); self.b = Builder(); self.n = 0

    def tearDown(self):
        shutil.rmtree(self.state)

    def runs(self, day, count, cw):
        for i in range(count):
            self.n += 1; aid = f'a{self.n}'
            self.b.con.execute('INSERT INTO subagent_runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                               (aid, f's{self.n}', 'scout', 'd', f'task {self.n} unique words {self.n}', 'ok', 'claude-opus-5-5',
                                ts(day, 8 + i), ts(day, 8 + i), None, 'P'))
            self.b.con.execute('INSERT INTO responses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (f'r{self.n}', f's{self.n}', aid, 'scout', None, 'claude-opus-5-5', ts(day, 8 + i), 10, cw, 0, 0, 100, None, 'P'))

    def test_propose_approve_baseline(self):
        self.runs(5, 4, 50_000)
        pid = changes.propose({'check': 'model_mismatch', 'subject': 'scout', 'scope': {'agent_type': 'scout'}}, self.state)
        c = changes.approve(self.b.con, pid, self.state, now=ts(10))
        self.assertEqual(c['baseline']['runs'], 4)
        self.assertEqual(changes._load('proposals.jsonl', self.state)[0]['status'], 'approved')

    def test_evaluate_waits_then_keeps(self):
        self.runs(5, 4, 50_000)
        pid = changes.propose({'check': 'x', 'subject': 'scout', 'scope': {'agent_type': 'scout'}}, self.state)
        changes.approve(self.b.con, pid, self.state, now=ts(10))
        changes.applied(pid, ['agents/scout.md'], self.state, now=ts(10, 12))
        self.assertEqual(changes.evaluate(self.b.con, self.state, now=ts(13))[0]['verdict'], 'not_enough_data')
        self.runs(15, 4, 5_000)
        self.assertEqual(changes.evaluate(self.b.con, self.state, now=ts(20))[0]['verdict'], 'keep')

    def test_verdict_reverts_on_quality_drop(self):
        base = {'days': 14, 'runs': 10, 'success_rate': 0.95, 'opus_eq_per_run': 100}
        after = {'days': 7, 'runs': 10, 'success_rate': 0.70, 'opus_eq_per_run': 50}
        self.assertEqual(changes.verdict(base, after)[0], 'revert')

    def test_q_refuses_writes(self):
        with self.assertRaises(SystemExit):
            q.run('DELETE FROM responses')
        with self.assertRaises(SystemExit):
            q.run('SELECT 1; DROP TABLE responses')


if __name__ == '__main__':
    unittest.main()
