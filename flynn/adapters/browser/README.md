# Browser capture

flynn has its own Chrome window, driven by Selenium, for taking references from live products, websites and admin screens. The user browses in it themselves, and flynn snaps whatever tab is in front.

## Commands

| Command | What it does |
|---|---|
| `capture.py open [--url URL]` | Starts flynn's Chrome with its own profile (`~/.flynn/chrome-profile`). The first time, the user logs into each site themselves; Chrome keeps the logins. |
| `capture.py snap --note "commission page, row expanded" [--product ...] [--mhtml]` | Captures the front tab: `viewport.png` (2x on a Retina screen), `full.png`, the scrubbed live `page.html` and `meta.json`. It also records a `candidate` reference in the store. |
| `capture.py watch [--minutes 30] [--limit N] [--auto]` | Puts a floating **Snap** bar on every page in flynn's Chrome; a page is snapped only when the user presses it (or Alt+Shift+S). The bar is removed before each snap, so it never shows. `--auto` snaps on every navigation instead. flynn runs it in the background and stops it when the user is done. |
| `capture.py list` / `status` / `close` | Recent snaps, what's open, and quit. |

Snaps are saved in `$FLYNN/refs/snaps/<date>/<time>-<slug>/`. The product label is detected from the host name; pass `--product` when detection gets it wrong.

## Safety

- **Credentials:** flynn never asks for, reads or stores passwords, cookies or browser storage, and never copies the profile folder.
- **Scrubbing:** `page.html` is the live DOM with the risky parts removed: the contents of inline scripts (they often hold session data), csrf and token meta tags, password and hidden input values, token-like attributes and anything shaped like a JWT. `page.mhtml` (only with `--mhtml`) keeps the scripts, so it can contain tokens; it's for opening offline, and it stays local.
- **Real data:** snaps can show real customer data. `refs/` is git-ignored. Never upload, share or copy snaps into another project without asking the user.
- **Who drives it:** only the main flynn session runs the browser, because the user is the one clicking.

## Requirements

- Google Chrome.
- `selenium` 4 (`python3 -m pip install --user selenium`).

Selenium Manager downloads the ChromeDriver that matches Chrome into `~/.cache/selenium`. A ChromeDriver on PATH is deliberately ignored: Homebrew's copy (148) is older than Chrome (154) and fails to connect.

## Notes

- **VPN:** internal tools need whatever network access the computer already has, such as a VPN.
- **Panels that scroll inside a container:** `full.png` equals the viewport for these, because the page itself doesn't scroll. Scroll the container and snap again.
- Pop-up blocking is off (`--disable-popup-blocking`), because some admin tools open details in pop-up windows. A pop-up is a normal window: `snap` and `watch` take whichever window is in front.
