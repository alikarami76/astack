# All values below are fake. Never paste a real credential into a test.
from qindex import redact
from qindex.php import Symbol, extract_cron, extract_php
from qindex.ts import extract_ts
from qindex.java import extract_java

R = redact.REDACTED


def test_userinfo_allows_empty_user_and_stops_at_path():
    assert redact.scrub("redis://:fakepw@cache:6379/0") == f"redis://{R}@cache:6379/0"
    assert redact.scrub("mysql://fakeuser:fakepw@db/x") == f"mysql://{R}@db/x"
    # a path segment is never swallowed: the '@' in a query is not userinfo
    assert redact.scrub("http://h:8080/p?e=a@b.com") == "http://h:8080/p?e=a@b.com"


def test_secret_pairs_by_key_suffix():
    assert redact.scrub("https://h/x?access_token=fakeabc&page=2") == f"https://h/x?access_token={R}&page=2"
    assert redact.scrub("client_secret=fakezzz") == f"client_secret={R}"
    assert redact.scrub("api_key=fakekkk") == f"api_key={R}"
    assert redact.scrub("x?apikey=fake1&y=1") == f"x?apikey={R}&y=1"
    assert redact.scrub("db?user=u&password=fakepw&ssl=true") == f"db?user=u&password={R}&ssl=true"
    assert redact.scrub("pwd:fakepw") == f"pwd:{R}"


def test_secret_pair_keeps_pure_placeholders():
    # an env placeholder names where the secret lives; it is not the secret. A default after ':' is.
    assert redact.scrub("spring.datasource.password=${DB_PASSWORD}") == "spring.datasource.password=${DB_PASSWORD}"
    assert redact.scrub("app.token=${APP_TOKEN:}") == "app.token=${APP_TOKEN:}"
    assert redact.scrub("app.token=${APP_TOKEN:fakedefault}") == f"app.token={R}"


def test_basic_and_bearer_values():
    assert redact.scrub("Authorization: Basic ZmFrZTpmYWtl") == f"Authorization: Basic {R}"
    assert redact.scrub("Authorization: Bearer fake.tok3n-value") == f"Authorization: Bearer {R}"
    assert redact.scrub("'Bearer ' + token") == "'Bearer ' + token"  # no literal token: nothing to hide
    assert redact.scrub("Basic authentication") == "Basic authentication"


def test_quoted_sensitive_assignments():
    assert redact.scrub("__construct($password = 'fakepw')") == f"__construct($password = '{R}')"
    assert redact.scrub('array $auth = ["UserName" => "fakeuser", "Password" => "fakepw"]') == \
        f'array $auth = ["UserName" => "{R}", "Password" => "{R}"]'
    assert redact.scrub("login(?string $token = null)") == "login(?string $token = null)"


def test_negatives_are_untouched():
    for s in ("jdbc:mysql://h:3306/db?useSSL=false", "http://h:8080/p?e=a@b.com", "max-tokens=5",
              "https://example.com/path", "getByToken(string $token): ?Entity", "TOKEN_TTL"):
        assert redact.scrub(s) == s, s


def test_console_names_and_endpoint_paths_are_not_secrets():
    # real shapes from the indexes: colon-separated console command names and URL-valued endpoint constants
    for s in ("framework:mp:api:refresh:token:expire", "framework:shop:api-app:client-token:scope",
              "console_command framework:shop:set:password:user",
              "POST_NOTIFICATION_TOKEN_EP = '/notification/token/'", "GET_SELF_TOKEN = '/self-token/'",
              "SET_PASSWORD_ROUTE = `/account/set-password`", "RESET_TOKEN_URL = 'https://h/reset'"):
        assert redact.scrub(s) == s, s
    # a URL that carries a key in its userinfo (a Sentry-style DSN) is still a credential
    assert redact.scrub("f($dsn = 'https://fakekey@o1.example.io/42')") == f"f($dsn = '{R}')"
    assert redact.scrub("GET_SELF_TOKEN = '/self-token/?token=fake1'") == f"GET_SELF_TOKEN = '/self-token/?token={R}'"


def test_labels_enums_and_usernames_alone_are_not_secrets():
    for s in ("INDEX_USERNAME = 'username'", "USER_NAME = 'user_name'", "SET_PASSWORD: 'set_password'",
              "DOM_DATA_TAGS = { setPassword: 'set-password' }", "minPassword: 'رمز عبور کوتاه است'", "token: 'token'"):
        assert redact.scrub(s) == s, s
    # a username next to a password is part of the same credential
    assert redact.scrub('["UserName" => "fakeuser", "Password" => "fakepw"]') == f'["UserName" => "{R}", "Password" => "{R}"]'


def test_redact_value_by_name():
    assert redact.redact_value("API_KEY", "'fake'") == R
    assert redact.redact_value("framework.api-key", "${FRAMEWORK_API_KEY:}") == "${FRAMEWORK_API_KEY:}"
    assert redact.redact_value("TIMEOUT", "30") == "30"
    assert redact.redact_value("API_KEY", "") == ""


def test_php_constants_and_constructor_defaults_are_redacted():
    src = """<?php
namespace App\\Command;
class DigipolyFakeCommand {
    public const API_KEY = 'Basic ZmFrZTpmYWtlZmFrZQ==';
    const SECRET = 'fake-secret-value', OTHER = 'kept';
    public const LAST_RUN_KEY = 'plain';
    public function __construct($wsdl = "https://h/x.wsdl", array $authentication = ["UserName" => "fakeuser", "Password" => "fakepw"], $password = 'x') {}
}
"""
    s = {x.name: x for x in extract_php("src/Command/DigipolyFakeCommand.php", src)}
    assert s["API_KEY"].signature == f"API_KEY = {R}"
    assert s["SECRET"].signature == f"SECRET = {R}"
    assert s["OTHER"].signature == "OTHER = 'kept'" and s["LAST_RUN_KEY"].signature == "LAST_RUN_KEY = 'plain'"
    ctor = s["__construct"].signature
    for fake in ("fakeuser", "fakepw", "'x'", "ZmFrZ", "fake-secret-value"):
        assert fake not in repr(list(s.values())), fake
    assert '$wsdl = "https://h/x.wsdl"' in ctor and f"$password = '{R}'" in ctor


def test_cron_command_lines_are_scrubbed():
    [c] = extract_cron("config/crontab", "*/5 * * * * curl -s https://fakeuser:fakepw@h/hook?token=fake1\n")
    assert "fakepw" not in repr(c) and "fake1" not in repr(c) and c.extra["command"].startswith("curl -s https://")


def test_ts_sensitive_constant_is_redacted():
    src = "export const API_TOKEN = 'fake-token-value';\nexport const PAGE_SIZE = 20;\nexport const HOME = 'home';\n"
    s = {x.name: x for x in extract_ts("src/config.ts", src)}
    assert s["API_TOKEN"].signature == f"API_TOKEN = {R}" and s["API_TOKEN"].extra.get("value") in (None, R)
    assert "fake-token-value" not in repr(list(s.values()))
    assert s["HOME"].extra["value"] == "home" and s["HOME"].signature == "HOME = 'home'"


def test_ts_relative_endpoint_named_like_a_secret_keeps_its_path():
    src = "export const POST_ACCOUNT_SET_PASSWORD = 'password';\nexport const GET_SELF_TOKEN = 'self-token?token=fake1';\n"
    s = {x.name: x for x in extract_ts("src/pages/account/API/endpoint.ts", src)}
    assert s["POST_ACCOUNT_SET_PASSWORD"].kind == "endpoint"
    assert s["POST_ACCOUNT_SET_PASSWORD"].signature == "POST_ACCOUNT_SET_PASSWORD = 'password'"
    assert "fake1" not in repr(s["GET_SELF_TOKEN"])


def test_java_sensitive_constant_is_redacted():
    src = 'package a;\npublic class K {\n    public static final String CLIENT_SECRET = "fake-secret";\n    public static final int LIMIT = 5;\n}\n'
    s = {x.name: x for x in extract_java("src/main/java/a/K.java", src)}
    assert s["CLIENT_SECRET"].signature == f"CLIENT_SECRET = {R}" and s["LIMIT"].signature == "LIMIT = 5"
    assert "fake-secret" not in repr(list(s.values()))


def test_redact_symbols_scrubs_nested_extra():
    s = Symbol("x", "n", "n", 1, "n", {"a": ["mysql://u:fakepw@h/x"], "b": {"c": "q?token=fake1"}, "n": 3})
    redact.redact_symbols([s])
    assert s.extra == {"a": [f"mysql://{R}@h/x"], "b": {"c": f"q?token={R}"}, "n": 3}


# ---- round 2 (R1-R3). Every value below is fake. ----

FAKE32 = "aZ3kQ9xL2mN7pR4tV8wY1bC6dF0gH5jK"


def test_r1_new_sensitive_names():
    src = ("<?php\nnamespace App\\Controller;\nclass DataSeederController {\n"
           "    const AUTHORIZATION = 'fake-shared-header-value';\n"
           "    const VALIDATION_KEY = 'fakevalidation';\n"
           "    const SALT = 'fakesalt';\n"
           "    const HMAC_KEY = 'fakehmac', SIGNING_KEY = 'fakesign', CLIENT_KEY = 'fakeclient';\n"
           "    const AUTH_ENABLED = true;\n}\n")
    s = {x.name: x for x in extract_php("src/Controller/Core/DataSeederController.php", src)}
    for n in ("AUTHORIZATION", "VALIDATION_KEY", "SALT", "HMAC_KEY", "SIGNING_KEY", "CLIENT_KEY"):
        assert s[n].signature == f"{n} = {R}", n
    assert "fake" not in repr(list(s.values()))
    assert s["AUTH_ENABLED"].signature == "AUTH_ENABLED = true"
    assert redact.redact_value("AUTHORIZATION", "'authorization'") == "'authorization'"  # a value that repeats its name
    assert redact.redact_value("security.authorization.enabled", "false") == "false"


def test_r2_high_entropy_detector():
    assert redact.is_high_entropy(FAKE32)
    assert redact.is_high_entropy("sdk-FAKEa1B2c3D4e5F6g7H8i9J0k")
    for neg in ("seller-voucher-report-export-queue-name-x",  # 40+ char lowercase-hyphen slug
                "https://example.com/aZ3kQ9xL2mN7pR4tV8wY1b", "/api/v1/seller/vouchers/report/export",
                "برچسب فارسی برای نمایش به کاربر است", "SELLER_VOUCHER_REPORT_V2_EXPORT_QUEUE",
                "App\\Service\\Pricing\\FeaturedofferPriceCalculatorService", "com.example.ledger.pricing.CommissionRuleService",
                "partnerReportReportExportQueueName", "PartnerReportV2ReportExportHandler", "short1A",
                "SetOrderItemPriceNullCommand", "FixUserFullNameCommand"):
        assert not redact.is_high_entropy(neg), neg


def test_r2_high_entropy_constant_and_config_values_are_redacted_whatever_the_name():
    src = f"<?php\nclass K {{\n    const FEATURE_ID = '{FAKE32}';\n    const QUEUE = 'SELLER_VOUCHER_REPORT_V2_EXPORT_QUEUE';\n}}\n"
    s = {x.name: x for x in extract_php("src/K.php", src)}
    assert s["FEATURE_ID"].signature == f"FEATURE_ID = {R}" and FAKE32 not in repr(s)
    assert s["QUEUE"].signature == "QUEUE = 'SELLER_VOUCHER_REPORT_V2_EXPORT_QUEUE'"
    ts = {x.name: x for x in extract_ts("src/config.ts", f"export const GB_CLIENT = 'sdk-{FAKE32}';\n")}
    assert FAKE32 not in repr(ts)
    from qindex.java import extract_java_config
    cfg = {c.name: c for c in extract_java_config("src/main/resources/application.properties", f"app.feature.id={FAKE32}\napp.name=ledger\n")
           if c.kind == "config"}
    assert cfg["app.feature.id"].extra["value"] == R and cfg["app.name"].extra["value"] == "ledger"


def test_r3_url_kinds_get_the_value_scrubber_but_keep_their_paths():
    src = (f"export const GET_REPORT_API = '/v1/report?api_key=fakekkk&page=1';\n"
           f"export const GB_FEATURES_API = 'https://gb.example.com/api/features/{FAKE32}';\n"
           f"export const LOGIN_API = 'https://fakeuser:fakepw@h.example.com/login';\n")
    s = {x.name: x for x in extract_ts("src/API/endpoint.ts", src)}
    assert s["GET_REPORT_API"].extra["url"] == f"/v1/report?api_key={R}&page=1"
    assert s["GB_FEATURES_API"].extra["url"] == f"https://gb.example.com/api/features/{R}"
    assert s["GB_FEATURES_API"].signature == f"GB_FEATURES_API = 'https://gb.example.com/api/features/{R}'"
    assert "fakepw" not in repr(s["LOGIN_API"]) and s["LOGIN_API"].extra["url"].endswith("h.example.com/login")
    assert FAKE32 not in repr(list(s.values())) and "fakekkk" not in repr(list(s.values()))


def test_r2_jwt_anywhere():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.ZmFrZXNpZ25hdHVyZQ"
    assert redact.scrub(f"f($t = x('{jwt}'))") == f"f($t = x('{R}'))"


def test_r2_tokens_inside_constant_objects_urls_truncation_and_cron():
    long_tok = ("MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE" + FAKE32 + FAKE32)  # fake, 100 chars: the signature is cut at 80
    src = ("export const AGENT_IDS = {\n    DEFAULT: '" + FAKE32 + "',\n    OTHER: 'plain-name',\n};\n"
           "export const HOOK_URL = 'https://hooks.example.com/services/T0FAKE/B0FAKE/" + FAKE32 + "';\n")
    s = {x.name: x for x in extract_ts("src/config.ts", src)}
    assert FAKE32 not in repr(list(s.values())) and "plain-name" in s["AGENT_IDS"].signature
    assert s["HOOK_URL"].signature.startswith("HOOK_URL = 'https://hooks.example.com/services/")
    php = {x.name: x for x in extract_php("src/K.php", f'<?php\nclass K {{\n    const PUBLIC_PEM = "{long_tok}";\n}}\n')}
    assert php["PUBLIC_PEM"].signature == f"PUBLIC_PEM = {R}"
    [c] = extract_cron("config/crontab", f"0 * * * * curl -s https://h.example.com/hook/{FAKE32}\n")
    assert FAKE32 not in repr(c)


def test_sensitive_name_assignments_are_scrubbed_in_every_form():
    from qindex.redact import scrub
    for line in ["$password = 'fakepw12345';", "'api_key' => 'fakekey12345',", "define('DB_PASS', 'fakepw12345');",
                 "define(\"DB_PASSWORD\", \"fakepw12345\");", "$db_pass = \"fakepw12345\";", "apiToken: `fakepw12345`",
                 "  client_secret: 'fakepw12345'", "SECRET_KEY = \"fakepw12345\""]:
        out = scrub(line)
        assert "fakepw12345" not in out and "<redacted>" in out, line


def test_sensitive_name_assignments_negatives():
    from qindex.redact import scrub
    for line in ["$passwordLength = 12;", "$username = 'bob';", "'token_bucket' => 'shared';", "define('APP_NAME', 'shop');",
                 "$bypass = 'yes';", "define('DB_PASS', '${DB_PASS}');", "$password = '/account/password';"]:
        assert scrub(line) == line, line


def test_camelcase_pass_names_are_scrubbed():
    from qindex.redact import scrub
    for line in ["dbPass: 'fakepw12345'", "$dbPass = 'fakepw12345';", "'dbPass' => 'fakepw12345',", "userPass: \"fakepw12345\"",
                 "define('adminPass', 'fakepw12345');", "$db2Pass = 'fakepw12345';"]:
        out = scrub(line)
        assert "fakepw12345" not in out and "<redacted>" in out, line
    for line in ["$bypass = 'fakepw12345';", "$passLabel = 'Enter';", "passport: 'x'", "compass = 'x'", "$passwordLength = 12;"]:
        assert scrub(line) == line, line
