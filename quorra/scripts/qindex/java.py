# scripts/qindex/java.py
"""Java / Spring Boot symbol extraction (controllers, JPA entities, Kafka, schedulers, config) for the ledger service."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from qindex.names import norm_url
from qindex.php import Symbol, _Lines, _skip_string, _split_call, _split_top, _strip_comments
from qindex.redact import PURE_PLACEHOLDER, REDACTED, SENSITIVE, redact_symbols, scrub as _scrub

MODS = "public|protected|private|static|final|abstract|synchronized|native|default|strictfp|transient|volatile|sealed|non-sealed"
NOT_A_TYPE = "return|new|throw|throws|else|case|assert|yield|package|import|extends|implements"
_TYPE = r"[\w.$]+(?:\s*<[^;(){}=]*?>)?(?:\s*\[\s*\])*"
RE_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
RE_ANN = re.compile(r"@([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")
RE_TYPE = re.compile(r"(?<![\w.$@])((?:(?:public|protected|private|static|final|abstract|sealed|non-sealed|strictfp)\s+)*)"
                     r"(@\s*interface|class|interface|enum|record)\s+([A-Za-z_]\w*)")
RE_METHOD = re.compile(r"(?<![\w.$@<>])((?:(?:%s)\s+)*)(?:<[^;(){}]*?>\s+)?(?!(?:%s|%s)\b)(%s)\s+([A-Za-z_]\w*)\s*\("
                       % (MODS, MODS, NOT_A_TYPE, _TYPE))
RE_METHOD_TAIL = re.compile(r"\s*(?:throws\s+[\w.,\s<>?]+?)?\s*(\{|;|default\b)")
RE_FIELD = re.compile(r"(?<![\w.$@<>])((?:(?:%s)\s+)*)(?!(?:%s|%s)\b)(%s)\s+([A-Za-z_]\w*)\s*(=(?!=)|;)"
                      % (MODS, MODS, NOT_A_TYPE, _TYPE))
RE_ENV = re.compile(r"System\s*\.\s*getenv\s*\(\s*\"([^\"]+)\"")
RE_SEND = re.compile(r"\b(\w+)\s*\.\s*(?:send|sendDefault)\s*\(\s*\"((?:[^\"\\]|\\.)*)\"")
RE_PRODUCER_RECORD = re.compile(r"new\s+ProducerRecord\s*<[^;()]*?>\s*\(\s*\"((?:[^\"\\]|\\.)*)\"")
RE_KAFKA_TEMPLATE = re.compile(r"KafkaTemplate\s*<[^;(){}]*?>\s+(\w+)")
RE_PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
RE_STRING = re.compile(r'^"((?:[^"\\]|\\.)*)"$', re.S)
REPO_BASES = r"(?:Jpa|Crud|PagingAndSorting|ListCrud|ListPagingAndSorting|Mongo|Reactive\w*|Elasticsearch)Repository"
MAPPINGS = {"GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "PatchMapping": "PATCH", "DeleteMapping": "DELETE"}
STEREOTYPES = [({"RestController", "Controller"}, "controller"), ({"Service"}, "service"), ({"Repository"}, "repository"),
               ({"Entity"}, "entity"), ({"Configuration"}, "config"), ({"Component"}, "component")]
SUFFIXES = [("Controller", "controller"), ("Service", "service"), ("Repository", "repository"), ("Config", "config"),
            ("Configuration", "config"), ("Exception", "exception"), ("Listener", "listener"), ("Dto", "dto"), ("DTO", "dto"),
            ("Request", "dto"), ("Response", "dto")]
ENV_NAME = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::[^}]*)?\}")


@dataclass
class _Ann:
    name: str  # simple name
    start: int
    end: int
    args: str | None  # None: a marker annotation without parentheses


@dataclass
class _Type:
    start: int
    open: int
    end: int
    name: str
    fq: str
    decl: str
    kind: str
    anns: list = field(default_factory=list)
    body_depth: int = 0


def _mask_strings(text: str) -> str:
    """Same-length copy with the contents of string, char and text-block literals blanked (newlines kept)."""
    out, i, n = [], 0, len(text)
    blank = lambda s: re.sub(r"[^\n]", " ", s)  # noqa: E731
    while i < n:
        if text.startswith('"""', i):
            k = text.find('"""', i + 3)
            j = n if k < 0 else k + 3
            out.append(blank(text[i:j]))
            i = j
        elif text[i] in "'\"":
            j = _skip_string(text, i)
            out.append(text[i] + blank(text[i + 1:j - 1]) + text[i] if j - i >= 2 else text[i:j])
            i = j
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _annotations(text: str, code: str) -> list[_Ann]:
    anns, last_end = [], 0
    for m in RE_ANN.finditer(code):
        if m.start() < last_end or m.group(1) == "interface":
            continue
        j = m.end()
        while j < len(code) and code[j] in " \t\r\n":
            j += 1
        if j < len(code) and code[j] == "(":
            _, end = _split_call(code, j)
            anns.append(_Ann(m.group(1).split(".")[-1], m.start(), end, text[j + 1:end - 1]))
        else:
            end = m.end()
            anns.append(_Ann(m.group(1).split(".")[-1], m.start(), end, None))
        last_end = end
    return anns


def _attrs(args: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in _split_top(args or "", ","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"([A-Za-z_]\w*)\s*=(?!=)\s*(.*)$", part, re.S)
        if m:
            out[m.group(1)] = m.group(2).strip()
        elif "value" not in out:
            out["value"] = part
    return out


def _literal(expr: str) -> str | None:
    m = RE_STRING.match(expr.strip())
    return m.group(1) if m else None


def _values(expr: str | None, unknown: str | None = None) -> list[str]:
    """Strings in an annotation attribute: 'a', {'a', 'b'}, 'a' + CONST. A non-literal element becomes `unknown`
    (or its own source text when unknown is None)."""
    expr = (expr or "").strip()
    if not expr:
        return []
    if expr.startswith("{") and expr.endswith("}"):
        return [v for p in _split_top(expr[1:-1], ",") if p.strip() for v in _values(p, unknown)]
    parts = [p.strip() for p in _split_top(expr, "+")]
    if len(parts) == 1:
        lit = _literal(parts[0])
        return [lit if lit is not None else (unknown if unknown is not None else parts[0])]
    return ["".join(_literal(p) if _literal(p) is not None else (unknown or "{}") for p in parts)]


def _join_url(prefix: str, path: str) -> str:
    segs = [s.strip("/") for s in (prefix, path) if s.strip("/")]
    return "/" + "/".join(segs)


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _skip_angle(s: str, i: int) -> int:
    """s[i] == '<': index just past the matching '>'."""
    depth = 0
    while i < len(s):
        if s[i] == "<":
            depth += 1
        elif s[i] == ">":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(s)


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+,", ",", re.sub(r"\s+", " ", s)).strip()


def _type_kind(decl: str, name: str, ann_names: set[str], tail: str) -> str:
    for names, kind in STEREOTYPES:
        if ann_names & names:
            return kind
    if re.search(REPO_BASES + r"\b", tail):
        return "repository"
    if decl == "enum":
        return "enum"
    if decl == "annotation":
        return "annotation"
    for suffix, kind in SUFFIXES:
        if name.endswith(suffix) and name != suffix:
            return kind
    if re.search(r"\bextends\s+[\w.]*(?:Exception|Error|Throwable)\b", tail):
        return "exception"
    return "dto" if decl == "record" else decl


def extract_java(path: str, text: str) -> list[Symbol]:
    text = _strip_comments(text, hash_comments=False, text_blocks=True)
    code = _mask_strings(text)
    lines = _Lines(text)
    anns = _annotations(text, code)
    buf = list(code)
    for a in anns:
        for k in range(a.start, a.end):
            if buf[k] != "\n":
                buf[k] = " "
    struct = "".join(buf)  # code with annotations blanked: declarations read cleanly
    by_end = {a.end: a for a in anns}
    pm = RE_PACKAGE.search(text)
    pkg = pm.group(1) + "." if pm else ""

    depth, pair, stack = [0] * (len(struct) + 1), {}, []
    for i, ch in enumerate(struct):
        depth[i] = len(stack)
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pair[stack.pop()] = i
    depth[len(struct)] = len(stack)

    def attached(pos: int) -> list[_Ann]:
        out: list[_Ann] = []
        while True:
            j = pos
            while j > 0 and code[j - 1].isspace():
                j -= 1
            a = by_end.get(j)
            if a is None:
                return out
            out.insert(0, a)
            pos = a.start

    def find_body(i: int) -> int:
        d = 0
        while i < len(struct):
            ch = struct[i]
            if ch == "(":
                d += 1
            elif ch == ")":
                d -= 1
            elif ch == ";" and d == 0:
                return -1
            elif ch == "{" and d == 0:
                return i
            i += 1
        return -1

    out: list[Symbol] = []
    types: list[_Type] = []
    open_stack: list[_Type] = []
    for m in RE_TYPE.finditer(struct):
        decl = re.sub(r"\s+", "", m.group(2))
        decl = "annotation" if decl == "@interface" else decl
        if decl == "record" and not re.match(r"\s*[<(]", struct[m.end():]):
            continue
        o = find_body(m.end())
        if o < 0 or o not in pair:
            continue
        name = m.group(3)
        while open_stack and open_stack[-1].end <= m.start():
            open_stack.pop()
        fq = (open_stack[-1].fq if open_stack else pkg.rstrip(".")) + ("." if open_stack or pkg else "") + name
        a_list = attached(m.start())
        after = m.end()
        ws = re.match(r"\s*", struct[after:]).end()
        if struct[after + ws:after + ws + 1] == "<":
            after = _skip_angle(struct, after + ws)
        tail_full = _norm_ws(struct[m.end():o])
        tail = _norm_ws(struct[after:o])
        ext = re.search(r"\bextends\s+([\w.]+)", tail)
        ann_names = [a.name for a in a_list]
        kind = _type_kind(decl, name, set(ann_names), tail_full)
        t = _Type(m.start(), o, pair[o], name, fq, decl, kind, a_list, depth[o] + 1)
        types.append(t)
        open_stack.append(t)
        out.append(Symbol(kind, name, fq, lines.at(m.start(3)), f"{decl} {name}{(' ' + tail_full) if tail_full else ''}"[:240],
                          {"decl": decl, "extends": ext.group(1) if ext else "", "annotations": ann_names}))
    starts = [t.start for t in types]

    def owner(offset: int) -> _Type | None:
        i = bisect.bisect_right(starts, offset) - 1
        while i >= 0 and not (types[i].open < offset < types[i].end):
            i -= 1
        return types[i] if i >= 0 else None

    def member_owner(offset: int) -> _Type | None:
        t = owner(offset)
        return t if t is not None and depth[offset] == t.body_depth else None

    # methods (all visibilities are scanned: handlers may be package-private; only public ones are emitted)
    methods = []  # (owner, name, offset, anns, body_start, body_end)
    for m in RE_METHOD.finditer(struct):
        t = member_owner(m.start())
        if t is None:
            continue
        _, end = _split_call(struct, m.end() - 1)
        tm = RE_METHOD_TAIL.match(struct, end)
        if not tm:
            continue
        body_open = tm.end(1) - 1 if tm.group(1) == "{" else -1
        body_end = pair.get(body_open, -1) if body_open >= 0 else -1
        mods = m.group(1) or ""
        name = m.group(3)
        m_anns = attached(m.start())
        methods.append((t, name, m.start(3), m_anns, body_open, body_end))
        visible = "public" in mods or (t.decl in {"interface", "annotation"} and "private" not in mods)
        if visible:
            params = _norm_ws(struct[m.end():end - 1])
            out.append(Symbol("method", name, f"{t.fq}::{name}", lines.at(m.start(3)), f"{name}({params}): {_norm_ws(m.group(2))}"))

    def enclosing_method(offset: int):
        for x in methods:
            if x[4] >= 0 and x[4] < offset < x[5]:
                return x
        return None

    # constants and entity fields
    tables: dict[int, str] = {}
    for t in types:
        if t.kind == "entity" or "Entity" in [a.name for a in t.anns]:
            table_ann = next((a for a in t.anns if a.name == "Table"), None)
            tname = _literal(_attrs(table_ann.args).get("name", "")) if table_ann else None
            if tname:
                out.append(Symbol("table", tname, tname, lines.at(table_ann.start), f"table {tname}", {"entity": t.fq}))
            else:
                tname = _snake(t.name)
                out.append(Symbol("table", tname, tname, lines.at(t.start + struct[t.start:].find(t.name)), f"table {tname}",
                                  {"entity": t.fq, "implicit": True}))
            tables[id(t)] = tname
    for m in RE_FIELD.finditer(struct):
        t = member_owner(m.start())
        if t is None:
            continue
        mods = m.group(1) or ""
        name, jtype = m.group(3), re.sub(r"\s+", "", m.group(2))
        if ("static" in mods and "final" in mods) or t.decl in {"interface", "annotation"}:
            if "private" in mods:
                continue
            semi, d, i = None, 0, m.end(3)
            while i < len(code):
                ch = code[i]
                if ch in "([{":
                    d += 1
                elif ch in ")]}":
                    d -= 1
                elif ch == ";" and d == 0:
                    semi = i
                    break
                i += 1
            val = _norm_ws(text[m.end(3):semi]).lstrip("=").strip()[:80] if semi else ""
            out.append(Symbol("constant", name, f"{t.fq}::{name}", lines.at(m.start(3)), f"{name} = {val}" if val else name))
            continue
        if id(t) not in tables or "static" in mods or "transient" in mods:
            continue
        f_anns = {a.name: a for a in attached(m.start())}
        if f_anns.keys() & {"Transient", "OneToMany", "ManyToMany", "Embedded"}:
            continue
        col, explicit = None, False
        for key in ("Column", "JoinColumn"):
            if key in f_anns:
                col = _literal(_attrs(f_anns[key].args).get("name", ""))
                if col:
                    explicit = True
                    break
        if not col:
            joined = f_anns.keys() & {"ManyToOne", "OneToOne", "JoinColumn"}
            col = _snake(name) + ("_id" if joined else "")  # Spring Boot's default physical naming strategy
        table = tables[id(t)]
        out.append(Symbol("column", col, f"{table}.{col}", lines.at(m.start(3)), f"{jtype} {col}", {"type": jtype, "table": table, **({} if explicit else {"implicit": True})}))

    # routes
    def route(url: str, verb: str, handler: str, offset: int) -> None:
        out.append(Symbol("route", url, f"{verb} {url}", lines.at(offset), f"{verb} {url}",
                          {"method": verb, "url_norm": norm_url(url), "handler": handler}))

    prefixes: dict[int, list[str]] = {}
    for t in types:
        ra = next((a for a in t.anns if a.name == "RequestMapping"), None)
        at = _attrs(ra.args) if ra else {}
        prefixes[id(t)] = _values(at.get("value") or at.get("path"), "{}") or [""]
    for t, mname, off, m_anns, _, _ in methods:
        if any(a.name == "FeignClient" for a in t.anns):
            continue
        handler = f"{t.fq}::{mname}"
        for a in m_anns:
            verb = MAPPINGS.get(a.name)
            if a.name != "RequestMapping" and verb is None:
                continue
            at = _attrs(a.args)
            verbs = [verb] if verb else (re.findall(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", at.get("method", "")) or ["ANY"])
            paths = _values(at.get("value") or at.get("path"), "{}") or [""]
            for v in verbs:
                for p in paths:
                    for pre in prefixes[id(t)]:
                        route(_join_url(pre, p), v, handler, a.start)

    # messaging and jobs
    def topic_names(expr: str | None) -> list[tuple[str, str | None]]:
        res = []
        for v in _values(expr):
            pm_ = RE_PLACEHOLDER.fullmatch(v)
            if pm_:
                res.append((pm_.group(2) or pm_.group(1), pm_.group(1)))
            else:
                res.append((v, None))
        return res

    def listener(a: _Ann, handler: str) -> None:
        at = _attrs(a.args)
        group = _values(at.get("groupId"))
        for name, key in topic_names(at.get("topics") or at.get("topic")):
            extra = {"handler": handler}
            if group:
                extra["group_id"] = group[0]
            if key:
                extra["placeholder"] = key
            out.append(Symbol("consumer", name, name, lines.at(a.start), f"consumer {name}", extra))
        for pat in _values(at.get("topicPattern")):
            out.append(Symbol("consumer", pat, pat, lines.at(a.start), f"consumer {pat}", {"handler": handler, "pattern": True}))

    for t in types:
        for a in t.anns:
            if a.name == "KafkaListener":
                listener(a, t.fq)
    for t, mname, off, m_anns, _, _ in methods:
        handler = f"{t.fq}::{mname}"
        for a in m_anns:
            if a.name == "KafkaListener":
                listener(a, handler)
            elif a.name == "Scheduled":
                at = _attrs(a.args)
                for trigger in ("cron", "fixedRate", "fixedDelay", "fixedRateString", "fixedDelayString"):
                    if trigger not in at:
                        continue
                    vals = _values(at[trigger])
                    val = vals[0] if vals else at[trigger]
                    out.append(Symbol("cron", mname, handler, lines.at(a.start), f"{trigger} {val}",
                                      {"handler": handler, "trigger": trigger, "schedule": val}))
            elif a.name == "ConfigurationProperties":
                at = _attrs(a.args)
                for prefix in _values(at.get("prefix") or at.get("value")):
                    out.append(Symbol("config", prefix, prefix, lines.at(a.start), f"@ConfigurationProperties {prefix}", {"class": t.fq, "handler": handler}))
    for t in types:
        for a in t.anns:
            if a.name == "ConfigurationProperties":
                at = _attrs(a.args)
                for prefix in _values(at.get("prefix") or at.get("value")):
                    out.append(Symbol("config", prefix, prefix, lines.at(a.start), f"@ConfigurationProperties {prefix}", {"class": t.fq}))

    templates = set(RE_KAFKA_TEMPLATE.findall(code))

    def producer(topic: str, offset: int, via: str) -> None:
        em = enclosing_method(offset)
        t = owner(offset)
        handler = f"{em[0].fq}::{em[1]}" if em else (t.fq if t else "")
        out.append(Symbol("producer", topic, topic, lines.at(offset), f"producer {topic}", {"handler": handler, "via": via}))

    for m in RE_SEND.finditer(text):
        recv = m.group(1)
        if code[m.start(1):m.end(1)] == recv and (recv in templates or "kafka" in recv.lower()):
            producer(m.group(2), m.start(), recv)
    for m in RE_PRODUCER_RECORD.finditer(text):
        if code[m.start():m.start() + 3] == "new":
            producer(m.group(1), m.start(), "ProducerRecord")

    # configuration and environment
    for a in anns:
        if a.name != "Value" or a.args is None:
            continue
        for v in _values(_attrs(a.args).get("value")):
            for pm_ in RE_PLACEHOLDER.finditer(v):
                default = pm_.group(2)
                if default is not None:
                    default = REDACTED if SENSITIVE.search(pm_.group(1)) and default else _scrub(default)
                extra = {"default": default} if default is not None else {}
                out.append(Symbol("config", pm_.group(1), pm_.group(1), lines.at(a.start), f"@Value {pm_.group(1)}", extra))
    for m in RE_ENV.finditer(text):
        if code[m.start():m.start() + 6] == "System":
            out.append(Symbol("env", m.group(1), m.group(1), lines.at(m.start()), f"env {m.group(1)}"))
    return redact_symbols(out)


# ---------------------------------------------------------------- resources

def is_migration(path: str) -> bool:
    return re.search(r"(^|/)db/migration/|(^|/)changelog/", path) is not None


def extract_java_migration(path: str, text: str) -> list[Symbol]:
    name = path.rsplit("/", 1)[-1]
    found = re.findall(r"(?i)\b(?:CREATE|ALTER|DROP)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?[`\"]?(\w+)", text)
    found += re.findall(r"tableName\s*=\s*[\"'](\w+)[\"']", text)
    tables = list(dict.fromkeys(found))
    sig = f"migration {name}" + (f": {', '.join(tables)}" if tables else "")
    return redact_symbols([Symbol("migration", name, path, 1, sig, {"tables": tables})])


def _profile(path: str) -> str:
    m = re.match(r"application-([\w.\-]+?)\.(?:ya?ml|properties)$", path.rsplit("/", 1)[-1])
    return m.group(1) if m else ""


def _leaf(path: str, key: str, value: str, line: int, out: list[Symbol], seen_env: set) -> None:
    envs = ENV_NAME.findall(value)  # names only: a default value is never copied into the env symbol
    if SENSITIVE.search(key) and value and not PURE_PLACEHOLDER.match(value):
        value = REDACTED
    else:
        value = _scrub(value)
    extra = {"value": value}
    prof = _profile(path)
    if prof:
        extra["profile"] = prof
    out.append(Symbol("config", key, key, line, f"{key}={value}"[:160], extra))
    for env in envs:
        if env not in seen_env:
            seen_env.add(env)
            out.append(Symbol("env", env, env, line, f"env {env}", {"source": "config", "key": key}))


def _strip_yaml_comment(s: str) -> str:
    q = ""
    for i, ch in enumerate(s):
        if q:
            if ch == q:
                q = ""
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


RE_YKEY = re.compile(r"^(?:\"([^\"]+)\"|'([^']+)'|([^\s:#\-\[\]{}'\"][^:#]*?|-[^\s:][^:#]*?))\s*:(?:\s+(.*))?$")


def _yaml_leaves(path: str, text: str) -> list[Symbol]:
    out: list[Symbol] = []
    seen_env: set = set()
    stack: list[tuple[int, str]] = []
    skip = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("---") or raw.startswith("..."):
            stack, skip = [], None
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        body = _strip_yaml_comment(raw.strip())
        if skip is not None:
            if indent > skip:
                continue
            skip = None
        if body == "-" or body.startswith("- "):
            skip = indent
            continue
        m = RE_YKEY.match(body)
        if not m:
            continue
        key = m.group(1) or m.group(2) or m.group(3)
        val = (m.group(4) or "").strip()
        if key.startswith("<<"):
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if val == "":
            stack.append((indent, key))
            continue
        full = ".".join([k for _, k in stack] + [key])
        if re.fullmatch(r"[|>][+\-0-9]*", val):
            skip = indent
            val = ""
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        _leaf(path, full, val, lineno, out, seen_env)
    return out


def _properties_leaves(path: str, text: str) -> list[Symbol]:
    out: list[Symbol] = []
    seen_env: set = set()
    physical = text.splitlines()
    i = 0
    while i < len(physical):
        start, line = i + 1, physical[i]
        i += 1
        stripped = line.lstrip()
        if not stripped or stripped[0] in "#!":
            continue
        while line.rstrip().endswith("\\") and i < len(physical):
            line = line.rstrip()[:-1] + physical[i].lstrip()
            i += 1
        m = re.match(r"\s*([^=:\s]+)\s*[=:]?\s*(.*)$", line)
        if m:
            _leaf(path, m.group(1), m.group(2).strip(), start, out, seen_env)
    return out


def extract_java_config(path: str, text: str) -> list[Symbol]:
    base = path.rsplit("/", 1)[-1].lower()
    return redact_symbols(_yaml_leaves(path, text) if base.endswith((".yml", ".yaml")) else _properties_leaves(path, text))
