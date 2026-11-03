import sys
from pathlib import Path

FLYNN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "penpot" / "panel"))
import engines  # noqa: E402


def _turn(**kw):
    base = dict(prompt="hi", session="s1", system="SYS", allow=["Read"], deny=["Bash(git push:*)"], file_id="f1",
                cwd="/tmp", claude="/bin/claude")
    base.update(kw)
    return engines.Turn(**base)


def test_cli_default_args_unchanged():
    cmd = engines.CliEngine().cmd(_turn())
    assert cmd[:4] == ["/bin/claude", "-p", "hi", "--agent"] and cmd[4] == "flynn"
    assert "--model" not in cmd and "--effort" not in cmd
    i = cmd.index("--allowedTools")
    assert cmd[i + 1:i + 3] == ["Read", "mcp__desk"]
    assert '"desk"' in cmd[cmd.index("--mcp-config") + 1]


def test_sdk_default_options_unchanged():
    import pytest
    pytest.importorskip("claude_agent_sdk")
    o = engines.SdkEngine().options(_turn())
    assert list(o.mcp_servers) == ["desk"]
    assert o.allowed_tools == ["Read", "mcp__desk"]
    assert o.extra_args == {"agent": "flynn", "permission-prompts": "none"}
    assert o.model is None and o.effort is None and o.max_budget_usd is None


def test_new_fields_flow_through():
    t = _turn(agent="quorra", mcp={"studio": {"type": "stdio", "command": "x", "args": []}},
              model="sonnet", effort="low", budget_usd=1.0)
    cmd = engines.CliEngine().cmd(t)
    assert cmd[cmd.index("--agent") + 1] == "quorra"
    assert cmd[cmd.index("--model") + 1] == "sonnet" and cmd[cmd.index("--effort") + 1] == "low"
    assert "mcp__studio" in cmd and "mcp__desk" not in cmd
    import pytest
    pytest.importorskip("claude_agent_sdk")
    o = engines.SdkEngine().options(t)
    assert list(o.mcp_servers) == ["studio"] and "mcp__studio" in o.allowed_tools
    assert o.model == "sonnet" and o.effort == "low" and o.max_budget_usd == 1.0
    assert o.extra_args["agent"] == "quorra"


def test_perms_shared():
    import perms
    import panel_server
    assert panel_server.BASE_ALLOW is perms.BASE_ALLOW and panel_server.DENY is perms.DENY
    assert any("/code/" in r for r in perms.DENY) and "Bash(git push:*)" in perms.DENY


def test_empty_agent_means_no_named_agent():
    cmd = engines.CliEngine().cmd(_turn(agent=""))
    assert "--agent" not in cmd
