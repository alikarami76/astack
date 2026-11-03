import pathlib
import sys

import pytest

FLYNN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FLYNN / "adapters" / "orgs"))
import orgs  # noqa: E402

OWNER = "owner@flynn.local"


class FakeCanvas:
    """Teams and members the way the canvas backend reports them."""

    def __init__(self):
        self.accounts = {OWNER: "p-owner", "sara@flynn.local": "p-sara", orgs.BUILDER: "p-builder"}
        self.teams = {"t1": {"name": "flynn", "members": {OWNER: "owner"}},
                      "t2": {"name": "Letters", "members": {OWNER: "owner", "sara@flynn.local": "editor"}},
                      "t9": {"name": "Default", "members": {OWNER: "owner"}, "isDefault": True}}
        self.calls = []

    def __call__(self, cmd, p=None):
        self.calls.append(cmd)
        p = p or {}
        if cmd == "get-profile":
            return {"email": OWNER, "fullname": "the user"}
        if cmd == "get-teams":
            return [{"id": k, "name": v["name"], "isDefault": v.get("isDefault", False)} for k, v in self.teams.items()]
        t = self.teams[p["teamId"]]
        if cmd == "get-team-members":
            return [{"id": self.accounts[e], "email": e, "isOwner": r == "owner", "isAdmin": r in ("owner", "admin"),
                     "canEdit": r != "viewer"} for e, r in t["members"].items()]
        if cmd == "create-team-invitations":
            for e in p["emails"]:
                if e in self.accounts:                 # an existing account is added straight away
                    t["members"][e] = p["role"]
            return None
        email = next(e for e, i in self.accounts.items() if i == p["memberId"])
        if cmd == "update-team-member-role":
            t["members"][email] = p["role"]
        elif cmd == "delete-team-member":
            del t["members"][email]
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(orgs, "LOG", tmp_path / "orgs.log")
    return FakeCanvas(), tmp_path / "orgs.json"


def test_create_makes_the_creator_owner_and_puts_teams_in(env):
    rpc, path = env
    r = orgs.act("create", {"name": "Marketing Team", "teams": ["t1", "t2"]}, OWNER, rpc, path)
    d = orgs.load(path)
    o = d["orgs"][0]
    assert r["ok"] and o["name"] == "Marketing Team"
    assert o["members"] == [{"email": OWNER, "name": "the user", "role": "owner"}]
    assert o["teams"] == ["t1", "t2"]
    assert rpc.teams["t1"]["members"][orgs.BUILDER] == "editor"     # the builder keeps library copies in sync


def test_org_admins_become_team_admins_and_lose_only_what_flynn_granted(env):
    rpc, path = env
    orgs.act("create", {"name": "DC", "teams": ["t1", "t2"]}, OWNER, rpc, path)
    orgs.act("member.add", {"org": "DC", "email": "sara@flynn.local", "role": "admin"}, OWNER, rpc, path)
    assert rpc.teams["t1"]["members"]["sara@flynn.local"] == "admin"      # added
    assert rpc.teams["t2"]["members"]["sara@flynn.local"] == "admin"      # raised from editor
    orgs.act("member.role", {"org": "DC", "email": "sara@flynn.local", "role": "member"}, OWNER, rpc, path)
    assert "sara@flynn.local" not in rpc.teams["t1"]["members"]          # flynn added her: removed
    assert rpc.teams["t2"]["members"]["sara@flynn.local"] == "editor"     # she was an editor before: restored


def test_plain_members_get_no_team_access(env):
    rpc, path = env
    orgs.act("create", {"name": "DC", "teams": ["t1"]}, OWNER, rpc, path)
    orgs.act("member.add", {"org": "DC", "email": "sara@flynn.local"}, OWNER, rpc, path)
    assert "sara@flynn.local" not in rpc.teams["t1"]["members"]


def test_taking_a_team_out_takes_back_its_access(env):
    rpc, path = env
    orgs.act("create", {"name": "DC", "teams": ["t1"]}, OWNER, rpc, path)
    orgs.act("member.add", {"org": "DC", "email": "sara@flynn.local", "role": "admin"}, OWNER, rpc, path)
    orgs.act("team.remove", {"org": "DC", "team": "t1"}, OWNER, rpc, path)
    assert set(rpc.teams["t1"]["members"]) == {OWNER}
    assert orgs.load(path)["orgs"][0]["granted"] == {}


def test_a_team_belongs_to_one_org_and_members_cannot_manage(env):
    rpc, path = env
    orgs.act("create", {"name": "A", "teams": ["t1"]}, OWNER, rpc, path)
    orgs.act("create", {"name": "B"}, OWNER, rpc, path)
    with pytest.raises(orgs.OrgError, match="already belongs to A"):
        orgs.act("team.add", {"org": "B", "team": "t1"}, OWNER, rpc, path)
    orgs.act("member.add", {"org": "A", "email": "sara@flynn.local"}, OWNER, rpc, path)
    with pytest.raises(orgs.OrgError, match="owners and admins"):
        orgs.act("rename", {"org": "A", "name": "X"}, "sara@flynn.local", rpc, path)


def test_the_last_owner_stays(env):
    rpc, path = env
    orgs.act("create", {"name": "A"}, OWNER, rpc, path)
    with pytest.raises(orgs.OrgError, match="at least one owner"):
        orgs.act("member.role", {"org": "A", "email": OWNER, "role": "admin"}, OWNER, rpc, path)
    with pytest.raises(orgs.OrgError, match="at least one owner"):
        orgs.act("member.remove", {"org": "A", "email": OWNER}, OWNER, rpc, path)


def test_view_lists_my_orgs_and_teams_outside_any_org(env):
    rpc, path = env
    orgs.act("create", {"name": "DC", "teams": ["t1"]}, OWNER, rpc, path)
    v = orgs.view(orgs.load(path), rpc, OWNER)
    assert [o["name"] for o in v["orgs"]] == ["DC"]
    assert v["orgs"][0]["teams"] == [{"id": "t1", "name": "flynn", "access": True}]
    assert {t["id"] for t in v["teams"]} == {"t2", "t9"}
    assert orgs.view(orgs.load(path), rpc, "nobody@x")["orgs"] == []


def test_delete_takes_back_everything(env):
    rpc, path = env
    orgs.act("create", {"name": "DC", "teams": ["t1", "t2"]}, OWNER, rpc, path)
    orgs.act("delete", {"org": "DC"}, OWNER, rpc, path)
    assert orgs.load(path)["orgs"] == []
    assert orgs.BUILDER not in rpc.teams["t1"]["members"]


def test_logo_must_be_an_image():
    o = {"logo": ""}
    with pytest.raises(orgs.OrgError):
        orgs.set_logo(o, "javascript:alert(1)")
    orgs.set_logo(o, "data:image/png;base64,iVBORw0KGgo=")
    assert o["logo"].startswith("data:image/png")
