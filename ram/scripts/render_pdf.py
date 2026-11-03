"""Render a ram Markdown report to PDF with pdfsmith, next to the Markdown file.

Usage: python3 render_pdf.py <report.md> [--title T] [--subtitle S]
Writes <same folder>/<same name>.pdf and prints its path. The pdfsmith build folder is <folder>/pdf/ (gitignored).
The first '# ' heading becomes the cover title unless --title is given.
"""
import argparse, datetime as dt, os, re, shutil, subprocess, sys
from pathlib import Path

PDFSMITH = str(Path(__file__).resolve().parents[2] / 'pdfsmith' / 'bin' / 'pdfsmith')
NOWRAP = '<span style="hyphens:none;white-space:nowrap">{}</span>'


def prepare(md):
    title = None
    m = re.search(r'^# (.+)$', md, re.M)
    if m:
        title = m.group(1).strip()
        md = md[:m.start()] + md[m.end():]
    # keep hyphenated identifiers (agent names, skills, checks) whole: the theme hyphenates English words
    out = []
    in_code = False
    for line in md.split('\n'):
        if line.startswith('```'):
            in_code = not in_code
        if not in_code:
            parts = re.split(r'(`[^`]*`)', line)
            parts = [p if p.startswith('`') else re.sub(r'(?<![\w/.<>"=-])([a-z][a-z0-9]*(?:-[a-z0-9]+)+)(?![\w/.-])',
                                                          lambda x: NOWRAP.format(x.group(1)), p) for p in parts]
            line = ''.join(parts)
        out.append(line)
    return title, '\n'.join(out).strip() + '\n'


def render(md_path, title=None, subtitle=None):
    folder = os.path.dirname(os.path.abspath(md_path))
    name = os.path.splitext(os.path.basename(md_path))[0]
    h1, body = prepare(open(md_path).read())
    title = title or h1 or 'Claude Code Usage Review'
    build = os.path.join(folder, 'pdf', name)
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    open(os.path.join(build, 'content.md'), 'w').write(body)
    today = dt.date.today().isoformat()
    doc = (f'template: report\ntheme: neutral\nname: {name}\nlang: en\nmeta:\n'
           f'  title: "{title}"\n  subtitle: "{subtitle or ""}"\n  author: ram, Claude Code usage analyst\n'
           f'  date: {today}\ncover: true\ntoc: true\nheader: "{{title}}"\nfooter: "{{page}} / {{pages}}"\ncontent: content.md\n')
    open(os.path.join(build, 'doc.yaml'), 'w').write(doc)
    r = subprocess.run([PDFSMITH, 'build', build], capture_output=True, text=True)
    print(r.stdout.strip()[-600:], file=sys.stderr)
    pdfs = [f for f in os.listdir(os.path.join(build, 'out')) if f.endswith('.pdf')] if os.path.isdir(os.path.join(build, 'out')) else []
    if r.returncode != 0 or not pdfs:
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f'pdfsmith build failed for {md_path}')
    target = os.path.join(folder, name + '.pdf')
    shutil.copy(os.path.join(build, 'out', pdfs[0]), target)
    return target


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('md'); ap.add_argument('--title'); ap.add_argument('--subtitle')
    a = ap.parse_args()
    print(render(a.md, a.title, a.subtitle))
