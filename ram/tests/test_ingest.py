import os, shutil, sys, tempfile, unittest, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import ingest

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'root')


class IngestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, 'root')
        shutil.copytree(FIX, self.root)
        self.db = os.path.join(self.tmp, 'ram.db')
        self.stats = ingest.ingest(self.root, self.db)
        self.con = ingest.connect(self.db)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def q(self, sql, *a):
        return self.con.execute(sql, a).fetchall()

    def test_split_response_one_row_final_usage(self):
        self.assertEqual(self.q("SELECT output, cache_write_5m FROM responses WHERE msg_id='m1'"), [(50, 1000)])
        self.assertEqual(self.stats['responses_total'], 3)

    def test_tool_error_and_names(self):
        rows = dict(self.q('SELECT id, tool FROM tool_calls'))
        self.assertEqual(rows, {'t1': 'Bash', 't2': 'Agent:Explore'})
        self.assertEqual(self.q("SELECT is_error, target FROM tool_calls WHERE id='t1'"), [(1, 'ls')])
        self.assertEqual(self.q("SELECT result_chars FROM tool_calls WHERE id='t2'")[0][0] > 0, True)

    def test_signals(self):
        kinds = {k for (k,) in self.q('SELECT kind FROM signals')}
        self.assertTrue({'user_correction', 'user_interrupt', 'compaction', 'tool_error', 'turn_duration'} <= kinds)

    def test_session(self):
        self.assertEqual(self.q('SELECT project, title, turns, compactions FROM sessions'),
                         [('Demo', 'Report building', 2, 1)])

    def test_subagent(self):
        r = self.q('SELECT parent_session, agent_type, task_prompt, final_output, model, spawn_tool_use_id FROM subagent_runs')
        self.assertEqual(r, [('s1', 'Explore', 'find x', 'found it', 'claude-haiku-4-5-20251001', 't2')])
        self.assertEqual(self.q("SELECT agent_type, session_id FROM responses WHERE msg_id='m3'"), [('Explore', 's1')])

    def test_skipped_line_counted(self):
        self.assertEqual(self.stats['skipped_lines'], 1)

    def test_incremental(self):
        self.assertEqual(ingest.ingest(self.root, self.db)['new_response_lines'], 0)
        p = os.path.join(self.root, '-Users-jane-Projects-Demo', 's1.jsonl')
        with open(p, 'a') as f:
            f.write(json.dumps({'type': 'assistant', 'sessionId': 's1', 'timestamp': '2026-09-01T10:05:00Z',
                                'message': {'id': 'm4', 'model': 'claude-opus-5-5', 'usage': {'input_tokens': 1, 'output_tokens': 1},
                                            'content': []}}) + '\n')
        s = ingest.ingest(self.root, self.db)
        self.assertEqual((s['new_response_lines'], s['responses_total']), (1, 4))


if __name__ == '__main__':
    unittest.main()
