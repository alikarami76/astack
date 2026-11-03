import sys, os, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import cost

class CostTest(unittest.TestCase):
    def test_opus55_fresh(self):
        self.assertAlmostEqual(cost.dollars('claude-opus-5-5', 1e6, 0, 0, 0, 0), 4.0)
    def test_opus55_cache_read(self):
        self.assertAlmostEqual(cost.dollars('claude-opus-5-5', 0, 0, 0, 1e6, 0), 0.20)
    def test_cache_write_5m(self):
        self.assertAlmostEqual(cost.dollars('claude-opus-5', 0, 1e6, 0, 0, 0), 6.25)
    def test_sonnet_output(self):
        self.assertAlmostEqual(cost.dollars('claude-sonnet-5-5', 0, 0, 0, 0, 1e6), 10.0)
    def test_dated_haiku_id(self):
        self.assertTrue(cost.price_for('claude-haiku-4-5-20251001')[1])
    def test_opus_eq_sonnet(self):
        self.assertAlmostEqual(cost.opus_eq('claude-sonnet-5-5', 1e6, 0, 0, 0, 0), 0.5e6)
    def test_opus_eq_cache_read(self):
        self.assertAlmostEqual(cost.opus_eq('claude-opus-5-5', 0, 0, 0, 1e6, 0), 0.1e6)
    def test_unknown_model(self):
        p, known = cost.price_for('claude-mystery-9')
        self.assertFalse(known); self.assertEqual(p['in'], 10)
    def test_synthetic_is_free(self):
        self.assertEqual(cost.dollars('<synthetic>', 1e6, 0, 0, 0, 0), 0)
    def test_haiku55_short_prompt(self):
        self.assertAlmostEqual(cost.dollars('claude-haiku-5-5', 50_000, 0, 0, 0, 1e6), 0.005 + 0.50)
    def test_haiku55_long_prompt_rate(self):
        # 200K-token prompt switches every token of the request to the long rate card
        self.assertAlmostEqual(cost.dollars('claude-haiku-5-5', 0, 0, 0, 200_000, 1e6), 200_000 * 0.05 / 1e6 + 2.50)
    def test_haiku55_cache_write(self):
        self.assertAlmostEqual(cost.dollars('claude-haiku-5-5', 0, 80_000, 0, 0, 0), 80_000 * 0.10 * 1.25 / 1e6)
    def test_haiku55_known(self):
        self.assertTrue(cost.price_for('claude-haiku-5-5')[1])
    def test_opus_eq_haiku55_weight(self):
        self.assertAlmostEqual(cost.opus_eq('claude-haiku-5-5', 50_000, 0, 0, 0, 0), 0.025 * 50_000)
    def test_opus_eq_haiku55_long(self):
        self.assertAlmostEqual(cost.opus_eq('claude-haiku-5-5', 0, 0, 0, 200_000, 0), 0.125 * 0.1 * 200_000)
    def test_opus_eq_haiku45_unchanged(self):
        self.assertAlmostEqual(cost.opus_eq('claude-haiku-4-5', 1e6, 0, 0, 0, 0), 0.25e6)

if __name__ == '__main__': unittest.main()
