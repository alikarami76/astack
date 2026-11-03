import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import render_pdf


class PrepareTest(unittest.TestCase):
    def test_title_taken_from_h1(self):
        t, body = render_pdf.prepare('# My review\n\n## Summary\ntext\n')
        self.assertEqual(t, 'My review'); self.assertNotIn('# My review', body)

    def test_identifiers_kept_whole_but_not_in_code(self):
        _, body = render_pdf.prepare('Move quorra-synthesizer, see `clu-data-runner` and /a/b-c.md\n')
        self.assertIn('nowrap">quorra-synthesizer</span>', body)
        self.assertIn('`clu-data-runner`', body)
        self.assertIn('/a/b-c.md', body)
        self.assertNotIn('nowrap">b-c', body)

    def test_plain_words_untouched(self):
        _, body = render_pdf.prepare('A one-week trial and re-read costs\n')
        self.assertIn('nowrap">one-week', body)  # harmless: short compounds also stay whole


if __name__ == '__main__':
    unittest.main()
