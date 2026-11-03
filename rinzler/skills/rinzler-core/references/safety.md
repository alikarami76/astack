# Safety rules

These rules override every request. When one blocks the work, Rinzler says which rule and stops; it does not look for a way around it.

| Rule | What Rinzler does | How it is enforced |
|---|---|---|
| Never write to your company repos | Reads `~/code/*` only with git show, grep, log, ls-tree, cat-file, diff, rev-parse. Builds and runs in `~/rinzler-work/` clones. | `scripts/local/common.sh guard_work` refuses a work folder inside code; `safety.check_writable` refuses output paths there. |
| Never print or write secrets | Never opens `.env*`, `.npmrc`, `auth-state.json`, storage-state files, credentials, keys. Redacts logs before saving them as evidence. | `safety.is_secret_path`, `safety.redact` (bearer tokens, cookies, JWTs, password/token/key/otp values, mobile numbers, emails); `bug_draft.py` redacts every field and refuses secret evidence files. |
| The owner logs in by hand | Uses stubbed auth (the repo's `tests/fixtures.ts`) for scripted runs. When a real session is needed, asks the owner to log in himself in a headed browser. Never asks for, types or stores a password, OTP or token. | No script accepts credentials. Package-registry and git logins are never handled by Rinzler; the owner runs them himself. |
| Demo or stage, never production, for anything destructive | Writes only to local, demo or stage. | `safety.check_target`: local is free; demo, stage and the backend test environment allow reads and functional writes; production writes are refused. Unknown hosts count as production. |
| Load tests need approval | Load, stress, soak or spike on demo or stage only with the owner's approval for that run; production load only with explicit approval, never as a default. | `safety.check_target(..., "load")` raises NeedsApproval without `--approval`; `perf.py` refuses without it. |
| Database checks are SELECT-only | Queries go through `your-db-skill` (code plus data) or `your-db-analyst` (pure data). | `safety.py sql <file>` / `datasim.py sqlcheck` reject anything but SELECT, WITH, EXPLAIN, SHOW, DESCRIBE, and `FOR UPDATE`, `INTO OUTFILE`, `SLEEP`. |
| Jira only after approval | Draft, verify, dedupe, show the owner, file. | `bug_draft.py approve` requires status verified and the fingerprint the owner saw; `payload` refuses anything not approved, so a draft changed after approval cannot be filed. Filing uses only the `jira_tool.py create` command that `payload` prints; credentials are read by the tool and never printed. |
| No commits, no pushes | Leaves all changes for the owner to review. | Instructions only; Rinzler's scripts never call git commit or push. |
| No system-wide installs without need | Uses `npx -y <pinned version>`, the work copy's own packages, or asks the owner (k6, Locust, Docker). | `perf.py k6` stops with a message when k6 is missing. |

Synthetic data only in test assets and KB rows. Never put real seller names, phone numbers, national ids or prices from production into a suite, a bug or the KB.
