# quorra

quorra is a builder and explainer agent for an engineering team, a sibling of the clu analyst agent. It researches how your code is written, turns that into tiered rules with file and line citations, and answers "how do we do X in repo Y" and "does X already exist?". It also explains how a feature works into a knowledge base, and builds small features and fixes in its own work clones with reviewers. It pushes, opens merge requests and writes to Jira only after you approve the exact plan.

## Configure

quorra works on any set of repos you list. Copy the examples in `config/` and edit them (the real files stay local and are not committed):

```
cp config/repos.example.json config/repos.json
cp config/checks.example.json config/checks.json
cp config/jira.example.json config/jira.json
```

- `config/repos.json`: one entry per repo, with its local clone `path`, its `gitlab` project path, its default `ref` and its `lang` (`php`, `ts` or `java`). Override the file with `QUORRA_REPOS`.
- `config/checks.json`: the lint, test and local-app commands for each kind of repo, used by `work.py`. Override with `QUORRA_CHECKS`.
- `config/jira.json`: the Jira helper script, the one project key quorra may write to and the transition names. Override with `QUORRA_JIRA_CONFIG`.
- `QUORRA_GITLAB_URL`: your GitLab base URL, for example `https://gitlab.example.com`.
- `QUORRA_GITLAB_READ_CREDENTIALS` and `QUORRA_GITLAB_WRITE_CREDENTIALS`: JSON files with `base_url` and `personal_access_token` (defaults `~/.config/quorra/gitlab-read.json` and `gitlab-write.json`).
- `QUORRA_CONFLUENCE_ENV`: an env file with `QUORRA_CONF_BASE_URL` and `QUORRA_CONF_TOKEN` (default `~/.config/quorra/confluence.env`), `QUORRA_CONFLUENCE_SPACE` for the one space quorra may publish to, and `QUORRA_CONFLUENCE_WRITER` for a Python file that defines `md_to_storage(markdown) -> str` (Markdown to Confluence storage format; you provide it).
- `QUORRA_FEATURE_KB`: the folder of your feature knowledge base (default `~/feature-knowledge-base`).
- `QUORRA_WORK_DIR`: where quorra keeps its own work clones (default `~/code/quorra-work`).
- `QUORRA_WRITABLE`: comma-separated repo names quorra may change (default `api-app,pricing,shared-lib,admin,web-app`, the repos in the example config; set it to your own names). Repos not on the list are read-only for quorra.

## Install
```
./install.sh
```
Then build the code indexes (lookup reports any repo without one instead of searching it); `install.sh` prints the exact command for your configured repos:
```
for r in <your repos>; do python3 scripts/index_repo.py $r --fetch; done
```
Open a new terminal and run `quorra` (or `claude --agent quorra`).

From any other Claude Code session, run `/quorra <question>` to bring quorra in as a teammate. The session spawns quorra as a subagent, relays its clarifying questions to you, and returns the cited result.

## Test
```
python3 -m pytest -q
```

## Prerequisites
- Python 3.12 or newer (the TS extractor uses f-string syntax from 3.12) and PyYAML
- git access to your GitLab
- a GitLab credentials file, for merge-request fetching
- optionally numpy, torch and transformers with the Qwen3 embedding model, for vector search

Read `docs/architecture.md` first.
