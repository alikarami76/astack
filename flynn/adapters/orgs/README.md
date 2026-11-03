# Organizations in flynn desk

An organization groups teams. It is flynn's own feature: Penpot's organizations are its paid Enterprise add-on, and flynn desk hides that add-on's buttons and prompts.

## What an org is

- A name and a logo.
- Its members, each an **owner**, **admin** or **member**.
- The teams that belong to it. A team belongs to one org at a time, and your personal team stays outside.

## What the roles do on the canvas

- **Owners and admins** become admins of every team in the org. flynn desk adds them, or raises their role, whenever a member, a role or a team changes.
- **Members** belong to the org and see it in the switcher. They join teams the normal way, team by team.
- **The builder account** joins every org team as an editor, so it can keep the org's library copies up to date there.
- **Taking access back.** flynn records every access it grants (`granted` in the state file). When someone leaves the org, loses the admin role, or a team leaves the org, flynn takes back only that access. Someone it added is removed from the team, and someone whose role it raised gets their old role back. Access you gave by hand is never touched, and neither is a team's owner.

## Shared resources

- **Fonts.** The theme font and Vazirmatn are uploaded to every team (`adapters/library/fonts.py`).
- **Libraries.** A canvas file can only use libraries from its own team, so each design system library gets a synced copy in every org team (in that team's "flynn libraries" project). A library sync with no team brings the original and every copy up to date. A build picks the copy in the open file's team (`push.py library_for`).
- **When this happens.** Creating an org, or bringing a team in, copies fonts and libraries in the background. It takes a minute or two per team.

## In the dashboard

- **The switcher.** It sits at the top of the sidebar, where Penpot's org row and "+ Create org" were. It lists your orgs and "No organization", and choosing one opens its first team.
- **The current org.** It follows the open team, so a reload or a shared link lands in the right org.
- **The team menu** shows only the current org's teams, plus your personal team.
- **New teams.** A team you create while an org is open joins that org.
- **Organization settings** has three tabs: **Members** (add by email, change a role, remove), **Teams** (bring in, take out, open) and **General** (name, logo, delete). Members who are not owners or admins see a read-only view.
- **New organization:** a name, and the teams to bring in.

## Command line

```bash
python3 adapters/orgs/orgs.py list
python3 adapters/orgs/orgs.py create "Acme Design" --team <team id> --team <team id>
python3 adapters/orgs/orgs.py member "Acme Design" add sara@example.com --role admin
python3 adapters/orgs/orgs.py team "Acme Design" add <team id>
python3 adapters/orgs/orgs.py sync [org] [--resources]     # re-apply access; with --resources also fonts and library copies
```

## Where things live

- **State:** `~/.flynn/orgs.json`.
- **Log:** `~/.flynn/logs/orgs.log`.
- **API** (panel server): `GET /flynn/api/orgs?me=<email>` and `POST /flynn/api/orgs` with an `action` (create, rename, logo, team.add, team.remove, member.add, member.role, member.remove, delete). Only owners and admins can change an org. Only owners can delete it or make someone an owner, and an org always keeps at least one owner.
- **Front end:** `stack/flynn-overlay/www/orgs.js`.
- **Tests:** `tests/test_orgs.py`.

## Limits

- **New accounts.** Adding someone without an account leaves a Penpot invitation in the local mail catcher. It is not sent to their real inbox.
- **Who makes the changes.** Every canvas change is made as the owner account (owner@flynn.local). That account must be an admin of the org's teams.
- **Trust.** The dashboard tells the server who is acting. That is fine for a local, single-user flynn desk. A shared server would need the canvas session checked on the server side first.
