# scripts/qindex/redact.py
"""Shared credential redaction for every extractor (PHP, TS, Java, cron).

Two layers:
- by NAME: a constant or config value whose name matches SENSITIVE is replaced whole (redact_value);
- by VALUE: scrub() removes credentials embedded in any text (URL userinfo, secret query/config pairs, quoted
  sensitive assignments such as `$password = '...'` or `'Password' => '...'`, and Basic/Bearer values).
redact_symbols() applies both to a list of symbols in place; every extractor calls it on its output."""
from __future__ import annotations

import re

REDACTED = "<redacted>"
SENSITIVE = re.compile(r"password|passwd|secret|token|api[-_.]?key|credential|private[-_.]?key|access[-_.]?key|dsn|"
                       r"authori[sz]ation|validation[-_.]?key|salt|client[-_.]?key|signing[-_.]?key|hmac", re.I)
PURE_PLACEHOLDER = re.compile(r"^\$\{[^}:]*:?\}$")

# user:pass@ after a scheme; the user may be empty (redis://:pw@h), and neither part may cross a '/' (no path spans)
USERINFO = re.compile(r"(://)[^/\s:@]*:[^@\s/]+@")
_KEY_END = (r"(?:password|passwd|pwd|secret|token|api[-_]?key|apikey|access[-_]?key|client[-_]?secret|client[-_]?key|"
            r"signing[-_]?key|validation[-_]?key|hmac)")
# key=value / key:value where the key ENDS in a secret word and is not glued to a preceding letter or digit. It may not
# continue a ':' '.' or '-' chain either, so console names like app:refresh:token:expire are left alone.
SECRET_PAIR = re.compile(r"(?i)(?<![A-Za-z0-9:.\-])([\w.-]*" + _KEY_END + r")([=:])(?![=:/>}])(\$\{[^}]*\}|[^&\s'\"`,;)}]+)")
# a quoted literal assigned to a key that ENDS in a secret word: $password = '..', 'Password' => "..", apiToken: `..`
# (POST_NOTIFICATION_TOKEN_EP = '/x' does not end in one; whole-name redaction of constants is redact_value's job)
_QKEY = (r"(?:(?:[\w.-]*[_.-])?pass(?![\w])|(?-i:[\w.-]*[a-z0-9]Pass(?![a-z]))|[\w.-]*(?:password|passwd|pwd|secret[-_.]?key|secret|token|api[-_.]?key|apikey|credentials?|private[-_.]?key|access[-_.]?key|dsn|"
         r"authori[sz]ation|validation[-_.]?key|salt|client[-_.]?key|signing[-_.]?key|hmac))")
# a path or URL is where something lives, not a credential; secrets inside it are caught by USERINFO and SECRET_PAIR
URL_LIKE = re.compile(r"^(?:https?://|/[A-Za-z0-9{$:_-]|\$\{|\./)")
QUOTED_SECRET = re.compile(r"(?i)((?<![\w$])\$?(['\"]?)(" + _QKEY + r")\2\s*(?:=>|=(?!=)|:)\s*)(['\"`])((?:\\.|(?!\4)[^\\])+)\4")
# a username is only redacted when the same text also carried a quoted password (["UserName" => .., "Password" => ..])
QUOTED_USER = re.compile(r"(?i)((?<![\w$])\$?(['\"]?)([\w.-]*user[-_]?name)\2\s*(?:=>|=(?!=)|:)\s*)(['\"`])((?:\\.|(?!\4)[^\\])+)\4")
# define('DB_PASS', 'x'): the name is a string argument, the value the next one
DEFINE_SECRET = re.compile(r"(?i)(\bdefine\(\s*(['\"])(" + _QKEY + r")\2\s*,\s*)(['\"`])((?:\\.|(?!\4)[^\\])+)\4")
JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
# a run of token characters, delimited so that URL path segments and query values are examined one at a time
_TOKEN_RUN = re.compile(r"(?<![A-Za-z0-9+_=-])[A-Za-z0-9+_=-]{24,}(?![A-Za-z0-9+_=-])")
_HE_CHARS = re.compile(r"[A-Za-z0-9+/=_-]{24,}")
_SEGMENTS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")
AUTH_SCHEME = re.compile(r"(?i)\b(basic|bearer)(\s+)([A-Za-z0-9+/=._~-]{8,})")


def is_high_entropy(tok: str) -> bool:
    """A single token of 24+ key characters that looks random rather than written: at least two of lower/upper/digit,
    and made of short fragments (a random key splits into 1-2 char pieces; camelCase, PascalCase and snake words into
    long ones). Paths, URLs, dotted or backslashed names, SCREAMING_SNAKE names and slugs are never high-entropy."""
    if not tok or not _HE_CHARS.fullmatch(tok) or tok.startswith(("/", "./")):
        return False
    if re.fullmatch(r"[A-Z0-9_]+", tok):  # a constant name used as a value
        return False
    classes = sum(bool(re.search(p, tok)) for p in ("[a-z]", "[A-Z]", "[0-9]"))
    if classes < 2:
        return False
    segs = _SEGMENTS.findall(tok)
    if not segs or sum(map(len, segs)) / len(segs) >= 3.0:
        return False
    # names like SetOrderItemPriceNullCommand have short pieces too, but most letters sit in real words
    wordy = sum(len(s) for s in segs if len(s) >= 4 and s[1:].islower())
    return wordy < len(tok) / 2


def _scrub_tokens(value: str) -> str:
    """Redact each high-entropy run inside a longer text (a URL path segment or query value), keeping the rest."""
    return _TOKEN_RUN.sub(lambda m: REDACTED if is_high_entropy(m.group(0)) else m.group(0), value)


def _auth(m: re.Match) -> str:
    tok = m.group(3)
    # an encoded credential or token has a digit, + / =, or an inner capital; a plain English word has none
    if re.search(r"[0-9+/=]", tok) or re.search(r"[A-Z]", tok[1:]):
        return f"{m.group(1)}{m.group(2)}{REDACTED}"
    return m.group(0)


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _not_a_credential(key: str, val: str) -> bool:
    """A path/URL without userinfo, human text (non-ASCII, e.g. a Persian label), or an enum value that just repeats
    its key (SET_PASSWORD: 'set_password') is not a credential."""
    if URL_LIKE.match(val) and "@" not in val:
        return True
    if any(ord(ch) > 127 for ch in val):
        return True
    return _squash(val) == _squash(key)


def _quoted(value: str) -> tuple[str, bool]:
    hit = False

    def sub(m: re.Match) -> str:
        nonlocal hit
        if _not_a_credential(m.group(3), m.group(5)):
            return m.group(0)
        hit = True
        return f"{m.group(1)}{m.group(4)}{REDACTED}{m.group(4)}"
    def sub_define(m: re.Match) -> str:
        nonlocal hit
        if _not_a_credential(m.group(3), m.group(5)):
            return m.group(0)
        hit = True
        return f"{m.group(1)}{m.group(4)}{REDACTED}{m.group(4)}"
    value = QUOTED_SECRET.sub(sub, value)
    value = DEFINE_SECRET.sub(sub_define, value)
    if hit:
        value = QUOTED_USER.sub(lambda m: m.group(0) if _not_a_credential(m.group(3), m.group(5))
                                else f"{m.group(1)}{m.group(4)}{REDACTED}{m.group(4)}", value)
    return value, hit


def scrub(value: str, quoted: bool = True) -> str:
    """Remove credentials embedded in a piece of text; everything else is left exactly as it was. Quoted literals
    assigned to a sensitive name ($password = '..', 'api_key' => '..', define('DB_PASS', '..')) are redacted by
    default, which is right for code, diffs and prose. quoted=False skips them, only for symbols whose quoted value is
    a URL by classification (endpoints, routes), where the value is a path and not a credential."""
    if not value:
        return value
    if quoted:
        value = _quoted(value)[0]
    value = USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", value)
    value = SECRET_PAIR.sub(lambda m: m.group(0) if PURE_PLACEHOLDER.match(m.group(3)) else f"{m.group(1)}{m.group(2)}{REDACTED}", value)
    value = JWT.sub(REDACTED, value)
    return AUTH_SCHEME.sub(_auth, value)


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] in "'\"`" and v[-1] == v[0]:
        return v[1:-1]
    return v[1:] if v[:1] in ("'", '"', "`") else v  # a signature cut at 80 chars loses its closing quote


def redact_value(name: str, value: str) -> str:
    """A value whose NAME is sensitive is dropped whole (a pure ${X} placeholder is kept); any other value is scrubbed."""
    raw = _unquote(value or "")
    if value and SENSITIVE.search(name or "") and not PURE_PLACEHOLDER.match(value.strip()):
        # never secret: an empty/boolean/null literal, or a value that only repeats its own name
        if raw.lower() in ("", "true", "false", "null", "none") or _squash(raw) == _squash(name.rsplit(".", 1)[-1]):
            return value
        return REDACTED
    if is_high_entropy(raw):  # R2: a random-looking key is redacted whatever its name
        return REDACTED
    return scrub(value)


def scrub_obj(x):
    if isinstance(x, str):
        return scrub(x)
    if isinstance(x, list):
        return [scrub_obj(v) for v in x]
    if isinstance(x, dict):
        return {k: scrub_obj(v) for k, v in x.items()}
    return x


NAMED_KINDS = {"constant", "config", "property"}
URL_KINDS = {"endpoint", "api_client", "ui_route", "route"}
TOKEN_KINDS = NAMED_KINDS | URL_KINDS | {"cron"}  # values here may embed a key: object members, URL segments, commands
VALUE_KEYS = ("value", "default")


def redact_symbols(symbols: list) -> list:
    """In place, per symbol:
    - constants/config/properties: the value is dropped whole when the NAME is sensitive or the value is a
      high-entropy token (redact_value), in the signature, the value/default extras and TS url_parts;
    - those kinds, URL kinds (endpoint, api_client, ui_route, route) and cron: every high-entropy run inside the
      signature or extras (an object member, a URL path segment, a query value, a command argument) is replaced and
      the rest of the text is kept;
    - everything: scrub() over name, fqname, signature and every string in extra."""
    for s in symbols:
        if s.kind in NAMED_KINDS:
            for prefix in (f"{s.name} = ", f"{s.name}="):
                if s.signature.startswith(prefix):
                    s.signature = prefix + redact_value(s.name, s.signature[len(prefix):])
                    break
            dropped = False
            for k in VALUE_KEYS:
                if isinstance(s.extra.get(k), str):
                    new = redact_value(s.name, s.extra[k])
                    dropped = dropped or (new == REDACTED and s.extra[k] != REDACTED)
                    s.extra[k] = new
            if "url_parts" in s.extra and (dropped or s.signature.endswith(f"= {REDACTED}")):
                s.extra["url_parts"] = [["lit", REDACTED]]  # TS constants keep literal pieces for url resolution
        q = s.kind not in URL_KINDS
        if s.kind in TOKEN_KINDS:
            if s.kind == "cron":  # a cron symbol without a console name is named after its command line
                s.name, s.fqname = _scrub_tokens(s.name), _scrub_tokens(s.fqname)
            s.signature = _scrub_tokens(s.signature)
            s.extra = _tokens_obj(s.extra)
        s.name, s.fqname, s.signature = scrub(s.name, q), scrub(s.fqname, q), scrub(s.signature, q)
        s.extra = scrub_obj(s.extra)
    return symbols


def _tokens_obj(x):
    if isinstance(x, str):
        return _scrub_tokens(x)
    if isinstance(x, list):
        return [_tokens_obj(v) for v in x]
    if isinstance(x, dict):
        return {k: _tokens_obj(v) for k, v in x.items()}
    return x
