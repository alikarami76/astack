# scripts/qindex/ts.py
"""TS/React symbol extraction tuned to web-app (exported consts, apiRequestObject, *_API endpoints)."""
from __future__ import annotations

import posixpath
import re

from qindex.names import norm_url
from qindex.php import Symbol, _Lines
from qindex.redact import redact_symbols

RE_EXPORT_FN = re.compile(r"^export\s+(default\s+)?(?:async\s+)?function\s+(\w+)\s*(\([^)]*\))?", re.M)
RE_EXPORT_CONST = re.compile(r"^export\s+const\s+(\w+)\s*(?::\s*([^=]+?))?\s*=\s*", re.M)
RE_TYPE = re.compile(r"^export\s+(?:declare\s+)?(interface|type|enum)\s+(\w+)", re.M)
RE_API_URL = re.compile(r"apiRequestObject\(\s*\{.*?\burl:\s*([\w.]+|'[^']*'|\"[^\"]*\"|`[^`]*`)", re.S)
RE_STRING = re.compile(r"""\s*(['"`])([^'"`]*)\1\s*;?\s*$""")
RE_OBJ_KEY = re.compile(r"^\s*['\"]?(\w+)['\"]?\s*:\s*(['\"`])(.*?)\2\s*,?\s*$", re.M)

# --- URL expression evaluation -------------------------------------------------------------
# An endpoint value is a list of parts: ("lit", text) | ("ref", NAME) | ("hole", name).
# Real forms in web-app: 'lit', `tpl/${id}`, ({id}) => `tpl/${id}`, SOME_CONST,
# `${BaseRoute}/x`, BASE + '/x', and constants imported from another file.
RE_ENDPOINT_SUFFIX = re.compile(r"_(API|EP|URL|ENDPOINT)$")
RE_ENDPOINT_PREFIX = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|UPDATE)_")
RE_TOP_CONST = re.compile(r"^(?:export\s+)?const\s+(\w+)\s*(?::\s*([^=\n]+?))?\s*=\s*(?!=)", re.M)
RE_IMPORT = re.compile(r"import\s*\{([^}]*)\}\s*from")
RE_IDENT = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*")
_OPEN, _CLOSE = "([{", ")]}"


def _skip_str(t: str, i: int) -> int:
    """Index just past the string/template literal starting at t[i]."""
    q, n, i = t[i], len(t), i + 1
    while i < n:
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if q == "`" and c == "$" and i + 1 < n and t[i + 1] == "{":
            depth, i = 1, i + 2
            while i < n and depth:
                if t[i] in "'\"`":
                    i = _skip_str(t, i)
                    continue
                depth += (t[i] == "{") - (t[i] == "}")
                i += 1
            continue
        if c == q:
            return i + 1
        i += 1
    return n


def _rhs_expr(text: str, start: int) -> str:
    """The expression that begins at `start` (after '='), up to a top-level ';' or a statement-ending newline."""
    n, i, depth = len(text), start, 0
    limit = min(n, start + 3000)
    while i < limit:
        c = text[i]
        if c in "'\"`":
            i = _skip_str(text, i)
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
            if depth < 0:
                break
        elif c == ";" and depth == 0:
            break
        elif c == "\n" and depth == 0:
            prev = text[start:i].rstrip()[-1:]
            nxt = text[i:].lstrip()[:1]
            if prev and prev not in "=>+?:,&|(" and nxt not in ("+", ".", "?", ":", "|", "&"):
                break
        i += 1
    return text[start:i].strip()


def _split_top(expr: str, sep: str) -> list[str]:
    out, depth, i, last = [], 0, 0, 0
    while i < len(expr):
        c = expr[i]
        if c in "'\"`":
            i = _skip_str(expr, i)
            continue
        if c in _OPEN:
            depth += 1
        elif c in _CLOSE:
            depth -= 1
        elif c == sep and depth == 0:
            out.append(expr[last:i])
            last = i + 1
        i += 1
    out.append(expr[last:])
    return out


def _arrow(expr: str):
    """If expr is an arrow function return (param_text, body) else None."""
    e = re.sub(r"^async\s+", "", expr)
    if e.startswith("("):
        depth, i = 0, 0
        while i < len(e):
            if e[i] in "'\"`":
                i = _skip_str(e, i)
                continue
            depth += (e[i] in _OPEN) - (e[i] in _CLOSE)
            i += 1
            if depth == 0:
                break
        params, rest = e[1:i - 1], e[i:]
    else:
        m = re.match(r"(\w+)", e)
        if not m:
            return None
        params, rest = m.group(1), e[m.end():]
    rest = re.sub(r"^\s*:\s*[^=]*?(?==>)", "", rest.lstrip()) if not rest.lstrip().startswith("=>") else rest.lstrip()
    if not rest.startswith("=>"):
        return None
    return params, rest[2:].strip()


def _template_parts(body: str, holes: set[str]) -> list:
    parts, i, buf = [], 0, ""
    while i < len(body):
        if body.startswith("${", i):
            depth, k = 1, i + 2
            while k < len(body) and depth:
                if body[k] in "'\"`":
                    k = _skip_str(body, k)
                    continue
                depth += (body[k] == "{") - (body[k] == "}")
                k += 1
            inner = body[i + 2:k - 1].strip()
            if buf:
                parts.append(("lit", buf))
                buf = ""
            call = re.match(r"([A-Za-z_$][\w$]*)\s*\(.*\)$", inner, re.S)
            if call and call.group(1) not in holes:  # `${OTHER_EP({ id })}`: splice the other builder's url
                parts.append(("ref", call.group(1)))
            elif RE_IDENT.fullmatch(inner) and inner.split(".")[0] not in holes:
                parts.append(("ref", inner.split(".")[-1]))
            else:
                parts.append(("hole", inner if re.fullmatch(r"\w+", inner) else "x"))
            i = k
        else:
            buf += body[i]
            i += 1
    if buf:
        parts.append(("lit", buf))
    return parts


def _expr_parts(expr: str, holes: frozenset = frozenset()) -> list | None:
    """Parse a URL-ish expression into parts, or None when it is not string-like."""
    expr = expr.strip()
    ar = _arrow(expr)
    if ar:
        pnames = set(re.findall(r"[A-Za-z_$][\w$]*", ar[0].split(":")[0] if "{" not in ar[0] else ar[0]))
        body = ar[1]
        if body.startswith("{"):  # block body: use the last `return <expr>;`
            rets = list(re.finditer(r"\breturn\s+", body))
            if not rets:
                return None
            pnames |= set(re.findall(r"\b(?:const|let|var)\s+(\w+)", body))  # locals like `params` are holes
            body = _rhs_expr(body, rets[-1].end())
        return _expr_parts(body, frozenset(pnames))
    while expr.startswith("(") and expr.endswith(")") and len(_split_top(expr[1:-1], "\0")) == 1:
        expr = expr[1:-1].strip()
    terms = _split_top(expr, "+")
    out: list = []
    for t in terms:
        t = t.strip()
        if not t:
            return None
        if t[0] in "'\"" and t[-1] == t[0] and _skip_str(t, 0) == len(t):
            out.append(("lit", t[1:-1]))
        elif t[0] == "`" and t[-1] == "`" and _skip_str(t, 0) == len(t):
            out += _template_parts(t[1:-1], set(holes))
        elif re.fullmatch(r"[A-Za-z_$][\w$]*\s*\(.*\)", t, re.S):  # call of another endpoint builder: FOO_EP({ id })
            out.append(("ref", re.match(r"[\w$]+", t).group(0)))
        elif RE_IDENT.fullmatch(t):
            out.append(("hole", t) if t.split(".")[0] in holes else ("ref", t.split(".")[-1]))
        else:
            return None
    return out


def _resolve_parts(parts: list, table: dict, seen: frozenset = frozenset()) -> list:
    """Substitute refs found in `table` (name -> parts); unknown refs stay as refs."""
    out: list = []
    for kind, val in parts:
        if kind == "ref" and val in table and val not in seen:
            out += _resolve_parts(table[val], table, seen | {val})
        else:
            out.append((kind, val))
    return out


def _render(parts: list) -> str:
    return "".join(v if k == "lit" else "{" + v + "}" for k, v in parts)


def _clean_url(url: str) -> str:
    return re.sub(r"^\./", "/", url.strip())


def _url_extra(parts: list) -> dict:
    """Fully resolved -> {url, url_norm}; otherwise keep the parts so resolve_api_urls can finish the job."""
    if any(k == "ref" for k, _ in parts):
        refs = [v for k, v in parts if k == "ref"]
        e: dict = {"url_parts": [list(p) for p in parts]}
        if len(parts) == 1:
            e["url_ref"] = refs[0]
        return e
    url = _clean_url(_render(parts))
    return {"url": url, "url_norm": norm_url(url)}


def _classify_const(name: str, path: str, parts: list | None, rhs: str, type_ann: str | None) -> str:
    if re.match(r"use[A-Z]", name):
        return "hook"
    if re.fullmatch(r"[A-Z0-9_]+", name):
        arrayish = rhs[:1] in "[{"
        if name.endswith("_ROUTE"):
            return "ui_route"
        if not arrayish and parts is not None and (
                RE_ENDPOINT_SUFFIX.search(name)
                or (RE_ENDPOINT_PREFIX.match(name) and any(k != "ref" for k, _ in parts))):
            return "endpoint"
        return "constant"
    if name[0].isupper() and (path.endswith(".tsx") or (type_ann and "FC" in type_ann)):
        return "component"
    return "function"


RE_IMPORT_FULL = re.compile(r"import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")


def _module_stem(spec: str, path: str) -> str | None:
    """Repo-relative module path (no extension) for a relative or `@/` import; None for packages."""
    if spec.startswith("@/"):
        base = "src/" + spec[2:]
    elif spec.startswith("."):
        base = posixpath.join(posixpath.dirname(path), spec)
    else:
        return None
    return re.sub(r"\.(tsx?|jsx?)$", "", posixpath.normpath(base))


def _imports(text: str, path: str) -> tuple[dict[str, str], dict[str, str]]:
    """({local alias: exported name}, {exported name: module stem}) from `import { A as B } from '...'`."""
    aliases: dict[str, str] = {}
    modules: dict[str, str] = {}
    for m in RE_IMPORT_FULL.finditer(text):
        stem = _module_stem(m.group(2), path)
        for item in m.group(1).split(","):
            mm = re.match(r"\s*(?:type\s+)?(\w+)(?:\s+as\s+(\w+))?\s*$", item)
            if not mm:
                continue
            if mm.group(2):
                aliases[mm.group(2)] = mm.group(1)
            if stem:
                modules[mm.group(1)] = stem
    return aliases, modules


def extract_ts(path: str, text: str) -> list[Symbol]:
    lines = _Lines(text)
    out: list[Symbol] = []
    aliases, modules = _imports(text, path)

    def unalias(parts: list) -> list:
        return [(k, aliases.get(v, v)) if k == "ref" else (k, v) for k, v in parts]

    # Every top-level string-like const (exported or not) so same-file refs like `${BaseRoute}/x` resolve.
    local: dict[str, list] = {}
    for m in RE_TOP_CONST.finditer(text):
        if not text.startswith("apiRequestObject(", m.end()):
            ps = _expr_parts(_rhs_expr(text, m.end()))
            if ps is not None and not (len(ps) == 1 and ps[0] == ("ref", m.group(1))):
                local[m.group(1)] = unalias(ps)

    for m in RE_EXPORT_FN.finditer(text):
        name = m.group(2)
        kind = "hook" if re.match(r"use[A-Z]", name) else (
            "component" if name[0].isupper() and path.endswith(".tsx") else "function")
        out.append(Symbol(kind, name, name, lines.at(m.start(2)), f"function {name}{m.group(3) or '()'}"))
    for m in RE_EXPORT_CONST.finditer(text):
        name, type_ann = m.group(1), m.group(2)
        line_end = text.find("\n", m.end())
        rhs_line = text[m.end(): line_end if line_end != -1 else len(text)]
        rhs = _rhs_expr(text, m.end())
        parts = _expr_parts(rhs) if not rhs.startswith("apiRequestObject(") else None
        if parts is not None:
            parts = unalias(parts)
            parts = _resolve_parts(parts, {k: v for k, v in local.items() if k != name})
        kind = _classify_const(name, path, parts, rhs, type_ann)
        extra: dict = {}
        final_parts = parts
        sig = f"const {name}"
        sm = RE_STRING.match(rhs_line)
        if sm and sm.group(1) == "`" and "${" in sm.group(2):
            sm = None  # templates with placeholders go through the parts evaluator
        if kind in ("endpoint", "ui_route", "constant") and sm:
            extra = {"url": sm.group(2), "url_norm": norm_url(_clean_url(sm.group(2)))} if kind == "endpoint" else {"value": sm.group(2)}
            sig = f"{name} = '{sm.group(2)}'"
        elif kind in ("endpoint", "ui_route", "constant"):
            sig = f"{name} = {re.sub(r'\\s+', ' ', rhs)[:80]}"
            if kind == "endpoint" and parts:
                extra = _url_extra(parts)
            elif kind == "constant" and parts and all(k == "lit" for k, _ in parts):
                extra = {"value": _render(parts)}
        if rhs.startswith("apiRequestObject("):
            um = RE_API_URL.match(text, m.end())
            kind, extra, final_parts = "api_client", {}, None
            if um:
                ref = um.group(1)
                ps = _expr_parts(ref)
                if ps is not None:
                    ps = _resolve_parts(unalias(ps), local)
                    extra = _url_extra(ps)
                    final_parts = ps
            sig = f"api {name}"
        if name.endswith("_TRANSLATIONS"):
            close = text.find("\n};", m.end())
            body = text[m.end(): close if close != -1 else len(text)]
            for km in RE_OBJ_KEY.finditer(body):
                out.append(Symbol("i18n_key", km.group(1), f"{name}.{km.group(1)}", lines.at(m.end() + km.start(1)), km.group(3)))
            kind = "constant"
        if kind in ("endpoint", "ui_route", "constant", "api_client"):
            extra["file"] = path
            if final_parts:  # persisted so resolve_api_urls can be re-run from stored rows alone
                extra["url_parts"] = [list(p) for p in final_parts]
                rf = {v: modules[v] for k, v in final_parts if k == "ref" and v in modules}
                if rf:
                    extra["ref_files"] = rf
        out.append(Symbol(kind, name, name, lines.at(m.start(1)), sig, extra))
    for m in RE_TYPE.finditer(text):
        out.append(Symbol("type", m.group(2), m.group(2), lines.at(m.start(2)), f"{m.group(1)} {m.group(2)}"))
    return redact_symbols(out)


def _stem(file: str) -> str:
    st = re.sub(r"\.(tsx?|jsx?)$", "", file)
    return st[:-6] if st.endswith("/index") else st


def resolve_api_urls(symbols: list[Symbol]) -> None:
    """Fill url/url_norm on endpoints and api_clients from their persisted `url_parts`.

    Works on rows rebuilt from storage (kind, name, extra only). A name defined in several files is
    resolved by the file the referrer imports, else the referrer's directory; when that is still
    unclear and the definitions disagree, the symbol is marked extra["ambiguous"] and left unresolved.
    """
    defs: dict[str, list[Symbol]] = {}
    for s in symbols:
        if s.kind in ("endpoint", "constant", "ui_route") and ("url_parts" in s.extra or "url" in s.extra or "value" in s.extra):
            defs.setdefault(s.name, []).append(s)
    memo: dict[int, tuple[list, bool]] = {}

    def own_parts(s: Symbol) -> list:
        if "url_parts" in s.extra:
            return [tuple(p) for p in s.extra["url_parts"]]
        return [("lit", s.extra.get("url", s.extra.get("value", "")))]

    def pick(name: str, ref_file: str, ref_files: dict) -> Symbol | None | bool:
        cands = defs.get(name, [])
        if len(cands) <= 1:
            return cands[0] if cands else None
        want = ref_files.get(name)
        if want:
            hit = [c for c in cands if _stem(c.extra.get("file", "")) == want]
            if len(hit) == 1:
                return hit[0]
        d = posixpath.dirname(ref_file)
        hit = [c for c in cands if ref_file and posixpath.dirname(c.extra.get("file", "")) == d]
        if len(hit) == 1:
            return hit[0]
        texts = {_final(c, frozenset())[0] and _text(_final(c, frozenset())[0]) for c in cands}
        if len(texts) == 1 and None not in texts:
            return cands[0]
        return False  # ambiguous

    def _text(parts: list) -> str | None:
        return None if any(k == "ref" for k, _ in parts) else _render(parts)

    def _final(s: Symbol, stack: frozenset) -> tuple[list, bool]:
        """(fully substituted parts, ambiguous?) for one symbol."""
        key = id(s)
        if key in memo:
            return memo[key]
        out: list = []
        amb = False
        file, rfs = s.extra.get("file", ""), s.extra.get("ref_files", {})
        for k, v in own_parts(s):
            if k != "ref":
                out.append((k, v))
                continue
            tgt = pick(v, file, rfs) if id(s) not in stack else None
            if tgt is False:
                amb = True
                out.append((k, v))
            elif tgt is None or tgt is s or id(tgt) in stack:
                out.append((k, v))
            else:
                sub, a = _final(tgt, stack | {id(s)})
                out += sub
                amb = amb or a
        memo[key] = (out, amb)
        return memo[key]

    for s in symbols:
        if s.kind not in ("endpoint", "api_client") or "url_parts" not in s.extra:
            continue
        parts, amb = _final(s, frozenset())
        unresolved = [v for k, v in parts if k == "ref"]
        s.extra.pop("ambiguous", None)
        if unresolved:
            for key in ("url", "url_norm"):
                s.extra.pop(key, None)
            if amb:
                s.extra["ambiguous"] = True
            if len(s.extra["url_parts"]) == 1:
                s.extra["url_ref"] = s.extra["url_parts"][0][1]
            continue
        url = _clean_url(_render(parts))
        s.extra.update(url=url, url_norm=norm_url(url))
