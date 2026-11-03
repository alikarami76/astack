# scripts/qindex/php.py
"""PHP symbol extraction with patterns tuned to framework repos (PSR-4, one class per file)."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from qindex.names import norm_url
from qindex.redact import redact_symbols


@dataclass
class Symbol:
    kind: str
    name: str
    fqname: str
    line: int
    signature: str = ""
    extra: dict = field(default_factory=dict)


ROLE_SUFFIXES = [("Entity", "entity"), ("Repository", "repository"), ("Service", "service"), ("Command", "command"),
                 ("Controller", "controller"), ("View", "view"), ("Form", "form"), ("DTO", "dto"), ("Dto", "dto"),
                 ("Enums", "enum"), ("Enum", "enum"), ("Listener", "listener"), ("Model", "model"),
                 ("Exception", "exception"), ("UseCase", "usecase"), ("Validator", "validator")]

RE_NS = re.compile(r"^\s*namespace\s+([\w\\]+)\s*;", re.M)
RE_CLASS = re.compile(r"^\s*(?:(?:final|abstract|readonly)\s+)*(class|interface|trait|enum)\s+(\w+)([^{]*)\{", re.M)
RE_METHOD = re.compile(r"^[ \t]*((?:(?:public|protected|private|static|final|abstract)\s+)*)function\s+&?\s*(\w+)\s*\(", re.M)
RE_RETURN = re.compile(r"\s*:\s*([?\w\\|&]+)")
RE_CONST = re.compile(r"^[ \t]*((?:(?:public|protected|private|final)\s+)*)const\s+(?:[?\w\\|&]+\s+)?(\w+)\s*=", re.M)
RE_FUNC = re.compile(r"\bfunction\s+&?\s*(\w+)")
RE_FLUENT = re.compile(r"->\s*(group|subgroup|end|get|post|put|patch|delete|addGet|addPost|addPut|addPatch|addDelete|addAction)\s*\(")
RE_CLASS_REF = re.compile(r"^([\w\\]+)::class$")
RE_CONST_REF = re.compile(r"^(?:[\w\\]+::)?([A-Z][A-Z0-9_]*)$")
RE_TABLE = re.compile(r"new\s+TableSchema\(\s*['\"](\w+)['\"]")
RE_COLUMN = re.compile(r"(\w+Column)::create\(\s*['\"](\w+)['\"]")
RE_ROUTE_DOC = re.compile(r"@Route\s+(GET|POST|PUT|PATCH|DELETE)\s+(\S+)")
RE_ROUTE_PAREN = re.compile(r"@Route\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"](\w+)['\"]\s*\)")
RE_ADD_CTRL = re.compile(r"->add(Internal)?(Get|Post|Put|Patch|Delete)?Controller(s)?\s*\(")
RE_STATIC_PROP = re.compile(r"(?:\bstatic\s+(?:[\w\\?]+\s+)?|\b(?:self|static)::)\$(\w+)\s*=\s*([\w\\]+)::class")
RE_PROP_REF = re.compile(r"^(?:self|static)::\$(\w+)$")
RE_STR = re.compile(r"^['\"]([^'\"]*)['\"]$")
RE_ADD_CRUD = re.compile(r"->addCrudController\(\s*\$\w+\s*,\s*['\"]([^'\"]+)['\"]\s*,\s*([\w\\]+)::class")
RE_SETNAME = re.compile(r"->setName\(\s*['\"]([^'\"]+)['\"]")
RE_ENV = re.compile(r"getenv\(\s*['\"](\w+)['\"]|\$_ENV\[\s*['\"](\w+)['\"]\s*\]")
RE_PARAM = re.compile(r"getParameter\(\s*['\"]([\w.\-]+)['\"]")
RE_CRON = re.compile(r"^\s*((?:[\d*/,\-]+\s+){4}[\d*/,\-]+)\s+(.+)$")


class _Lines:
    def __init__(self, text: str):
        self.starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def at(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


def _skip_string(text: str, i: int) -> int:
    """text[i] is a quote; return the index just past the closing quote."""
    q = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == q:
            return i + 1
        i += 1
    return len(text)


def _split_call(text: str, open_idx: int):
    """text[open_idx] == '('. Return ([(offset, arg_text)], index after the closing paren)."""
    args, depth, i, start = [], 0, open_idx, open_idx + 1
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            i = _skip_string(text, i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append((start, text[start:i]))
                return args, i + 1
        elif ch == "," and depth == 1:
            args.append((start, text[start:i]))
            start = i + 1
        i += 1
    return args, len(text)


def _split_top(text: str, sep: str) -> list[str]:
    parts, depth, i, start = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"":
            i = _skip_string(text, i)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _strip_comments(text: str, *, hash_comments: bool = True, text_blocks: bool = False) -> str:
    """Blank out // and # line comments and /* */ blocks (keeping newlines, so offsets and lines are unchanged).
    Java mode: hash_comments=False leaves '#' alone, text_blocks=True copies triple-quoted strings through untouched."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if text_blocks and text.startswith('"""', i):
            j = text.find('"""', i + 3)
            j = n if j < 0 else j + 3
            out.append(text[i:j])
            i = j
        elif ch in "'\"":
            j = _skip_string(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("/*", i) or text.startswith("//", i) or (hash_comments and ch == "#" and not text.startswith("#[", i)):
            if text.startswith("/*", i):
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
            else:
                j = text.find("\n", i)
                j = n if j < 0 else j
            out.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _resolve_url(expr: str, api_prefix: str = "") -> str:
    """Resolve 'literal' and CONST.'literal' concatenations; API_VERSION drops out, unknowns become {}."""
    out = []
    for part in _split_top(expr, "."):
        part = part.strip()
        if re.fullmatch(r"\$this\s*->\s*getApiPrefix\s*\(\s*\)", part):
            out.append(api_prefix or "{}")
            continue
        if len(part) >= 2 and part[0] in "'\"" and part[-1] == part[0]:
            out.append(part[1:-1])
            continue
        m = RE_CONST_REF.match(part)
        if m and m.group(1) == "API_VERSION":
            continue
        if m and m.group(1) == "API_INTERNAL_VERSION":
            out.append("/api/internal/v2")
            continue
        out.append("{}")
    return "".join(out)


def _is_url_expr(expr: str) -> bool:
    expr = expr.strip()
    return bool(expr) and (expr[0] in "'\"" or expr.startswith("$this->getApiPrefix(") or RE_CONST_REF.match(_split_top(expr, ".")[0].strip()) is not None)


def _role(name: str, decl: str) -> str:
    if decl == "enum":
        return "enum"
    for suffix, role in ROLE_SUFFIXES:
        if name.endswith(suffix) and name != suffix:
            return role
    return decl


def _extends(tail: str) -> str:
    m = re.search(r"extends\s+([\w\\]+)", tail)
    return m.group(1) if m else ""


def extract_php(path: str, text: str) -> list[Symbol]:
    raw = text  # docblock @Route annotations are read from the raw text; everything else from comment-free code
    text = _strip_comments(raw)
    lines = _Lines(text)
    ns_m = RE_NS.search(text)
    ns = ns_m.group(1) + "\\" if ns_m else ""
    out: list[Symbol] = []
    classes = []  # (start_offset, name, fq)
    for m in RE_CLASS.finditer(text):
        decl, name, tail = m.group(1), m.group(2), m.group(3)
        fq = ns + name
        classes.append((m.start(), name, fq))
        out.append(Symbol(_role(name, decl), name, fq, lines.at(m.start(2)), f"{decl} {name}{(' ' + tail.strip()) if tail.strip() else ''}",
                          {"decl": decl, "extends": _extends(tail)}))
    starts = [c[0] for c in classes]

    def owner(offset: int):
        i = bisect.bisect_right(starts, offset) - 1
        return classes[i] if i >= 0 else (0, "", ns.rstrip("\\"))

    main_fq = classes[0][2] if classes else ns.rstrip("\\")
    for m in RE_METHOD.finditer(text):
        mods = m.group(1) or ""
        if "private" in mods or "protected" in mods:
            continue
        _, cname, cfq = owner(m.start())
        _, end = _split_call(text, m.end() - 1)
        params = re.sub(r"\s+", " ", text[m.end():end - 1]).strip()
        rt = RE_RETURN.match(text, end)
        sig = f"{m.group(2)}({params})" + (f": {rt.group(1)}" if rt else "")
        out.append(Symbol("method", m.group(2), f"{cfq}::{m.group(2)}", lines.at(m.start(2)), sig))
    for m in RE_CONST.finditer(text):
        if "private" in (m.group(1) or ""):
            continue
        _, cname, cfq = owner(m.start())
        # the whole statement up to the first top-level ';' may declare several names
        depth, i = 0, m.end(2)
        while i < len(text):
            ch = text[i]
            if ch in "'\"":
                i = _skip_string(text, i)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            i += 1
        body = text[m.end(2):i]
        pieces = _split_top(body, ",")
        off = m.end(2)
        for idx, piece in enumerate(pieces):
            if idx == 0:
                name, val, name_off = m.group(2), piece.split("=", 1)[1], m.start(2)
            else:
                pm = re.match(r"\s*(\w+)\s*=(.*)$", piece, re.S)
                if not pm:
                    off += len(piece) + 1
                    continue
                name, val, name_off = pm.group(1), pm.group(2), off + pm.start(1)
            val = re.sub(r"\s+", " ", val).strip()[:80]
            out.append(Symbol("constant", name, f"{cfq}::{name}", lines.at(name_off), f"{name} = {val}"))
            off += len(piece) + 1
    tm = RE_TABLE.search(text)
    if tm:
        table = tm.group(1)
        out.append(Symbol("table", table, table, lines.at(tm.start(1)), f"table {table}", {"entity": main_fq}))
        for cm in RE_COLUMN.finditer(text, tm.end()):
            out.append(Symbol("column", cm.group(2), f"{table}.{cm.group(2)}", lines.at(cm.start(2)), f"{cm.group(1)} {cm.group(2)}",
                              {"type": cm.group(1), "table": table}))

    def route(url, method, handler, offset, more=None):
        out.append(Symbol("route", url, f"{method} {url}", lines.at(offset), f"{method} {url}",
                          {"method": method, "url_norm": norm_url(url), "handler": handler, **(more or {})}))

    for m in RE_ROUTE_DOC.finditer(raw):
        route(m.group(2), m.group(1), main_fq, m.start())
    for m in RE_ROUTE_PAREN.finditer(raw):
        route(m.group(1), m.group(2).upper(), main_fq, m.start())
    props = {pm.group(1): pm.group(2).split("\\")[-1] for pm in RE_STATIC_PROP.finditer(text)}
    for m in RE_ADD_CTRL.finditer(text):
        internal, plural = bool(m.group(1)), bool(m.group(3))
        verb = (m.group(2) or "any").upper()
        args, _ = _split_call(text, m.end() - 1)
        texts = [a[1].strip() for a in args]
        if len(texts) < 2:
            continue
        cm, pr = RE_CLASS_REF.match(texts[0]), RE_PROP_REF.match(texts[0])
        ctrl = cm.group(1).split("\\")[-1] if cm else props.get(pr.group(1), "") if pr else None
        if ctrl is None:
            continue
        extra = {"internal": True} if internal else {}
        if plural:
            prefix = ""
            if len(texts) > 2:
                lits = re.findall(r"['\"]([^'\"]*)['\"]", texts[2])
                prefix = lits[-1] if lits else ""
            for em in re.finditer(r"['\"]([^'\"]+)['\"]\s*=>\s*['\"](\w+)['\"]", texts[1]):
                route(prefix + em.group(1), verb, f"{ctrl}::{em.group(2)}" if ctrl else "", m.start(), extra)
            continue
        um = RE_STR.match(texts[1])
        if not um:
            continue
        am = RE_STR.match(texts[2]) if len(texts) > 2 else None
        action = am.group(1) if am and re.fullmatch(r"\w+", am.group(1)) else ""
        route(um.group(1), verb, f"{ctrl}::{action}" if ctrl and action else ctrl, m.start(), extra)
    for m in RE_ADD_CRUD.finditer(text):
        route(m.group(1), "CRUD", m.group(2).split("\\")[-1], m.start())
    # Fluent builder: ->group('/x')->subgroup('/y')->get('/z', View::class)->end().
    # group() replaces the prefix, subgroup() pushes, end() pops one (CQRSRouteCollectionListener.php:130-150).
    # OpenAPI collections (extends *OpenApiRouteCollection*) prefix every group with getApiPrefix() =
    # OpenAPIRouteCollectionListener::API_PREFIX ('/open-api') . '/v' . getApiVersion()
    # (AOpenApiRouteCollectionAction.php:31-34,112-116).
    api_prefix = ""
    vm = re.search(r"function\s+getApiVersion\s*\(\s*\)[^{]*\{\s*return\s+(\d+)\s*;", text)
    if vm and re.search(r"extends\s+[\w\\]*OpenApiRouteCollection", text, re.I):
        api_prefix = f"/open-api/v{vm.group(1)}"
    group, subs = "", []
    for m in re.finditer(RE_FLUENT.pattern + r"|\bfunction\s+&?\s*\w+", text):
        if m.group(1) is None:  # a new function body: builder stack starts fresh, the group carries over
            subs = []
            continue
        verb = m.group(1)
        args, _ = _split_call(text, m.end() - 1)
        texts = [a[1].strip() for a in args]
        if verb == "end":
            if subs:
                subs.pop()
        elif verb == "group" and texts:
            group = api_prefix + _resolve_url(texts[0])
        elif verb == "subgroup" and texts:
            subs.append(_resolve_url(texts[0]))
        elif verb == "addAction":
            if len(texts) >= 2 and _is_url_expr(texts[0]) and RE_CLASS_REF.match(texts[1]):
                methods = re.findall(r"['\"](\w+)['\"]", texts[2]) if len(texts) > 2 else ["get"]
                for meth in methods or ["get"]:
                    route(_resolve_url(texts[0], api_prefix), meth.upper(), RE_CLASS_REF.match(texts[1]).group(1).split("\\")[-1], m.start())
        elif len(texts) >= 2 and _is_url_expr(texts[0]) and RE_CLASS_REF.match(texts[1]):
            handler = RE_CLASS_REF.match(texts[1]).group(1).split("\\")[-1]
            if verb.startswith("add"):
                route(_resolve_url(texts[0], api_prefix), verb[3:].upper(), handler, m.start())
            else:
                route(group + "".join(subs) + _resolve_url(texts[0]), verb.upper(), handler, m.start())
    funcs = [(f.start(), f.group(1)) for f in RE_FUNC.finditer(text)]
    func_starts = [f[0] for f in funcs]
    for m in RE_SETNAME.finditer(text):
        _, cname, cfq = owner(m.start())
        i = bisect.bisect_right(func_starts, m.start()) - 1
        in_configure = i >= 0 and funcs[i][1] == "configure"
        if not (in_configure or cname.endswith("Command")):
            continue
        kind = "consumer" if re.search(r"Dirty|Consumer", cname) else "console_command"
        out.append(Symbol(kind, m.group(1), cfq, lines.at(m.start(1)), f"{kind} {m.group(1)}"))
    for m in RE_ENV.finditer(text):
        name = m.group(1) or m.group(2)
        out.append(Symbol("env", name, name, lines.at(m.start()), f"env {name}"))
    for m in RE_PARAM.finditer(text):
        out.append(Symbol("config", m.group(1), m.group(1), lines.at(m.start(1)), f"parameter {m.group(1)}"))
    return redact_symbols(out)


def is_cron_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return "cron" in base and not base.endswith((".php", ".ts", ".tsx", ".js"))


def extract_cron(path: str, text: str) -> list[Symbol]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        m = RE_CRON.match(line)
        if not m:
            continue
        cmd = m.group(2).strip()
        cm = re.search(r"console\s+([\w:\-]+)", cmd)
        name = cm.group(1) if cm else cmd[:80]
        out.append(Symbol("cron", name, name, i, re.sub(r"\s+", " ", m.group(1).strip()), {"command": cmd}))
    return redact_symbols(out)
