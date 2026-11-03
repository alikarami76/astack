# flynn

flynn is a design agent, a sibling of clu (analyst) and quorra (builder). It designs product UI, decks and release-note graphics, reports, newsletters and data visualizations, from a design system it has learned and a stored component library. It talks with you before it builds, agrees a design contract with you, audits its own work against approved references and a rubric, stores what it makes, and learns from every run.

flynn works inside **flynn desk**, a design app built on Penpot with a flynn tab next to Design, Prototype and Inspect, and it also runs from the terminal. It learns a design system by recording a live product or by reading code, and it ships a neutral `editorial` theme for documents people read.

## Install
```
./install.sh
```
Open a new terminal and run `flynn` (or `claude --agent flynn`).

From any other Claude Code session, run `/flynn <brief>` to bring flynn in as a teammate. Other agents can call it the same way with the Agent tool (`subagent_type: "flynn"`). `/flynn-review` reviews the changes flynn proposes to its design system or to itself.

## Test
```
python3 -m pytest -q
```

## Stage
The skeleton (knowledge store, flynn document format and validator, run records, proposals, agents and teammate mode), the HTML, PowerPoint and Canva adapters, charts, Studio and flynn desk (with the recorder, libraries, Letters and organizations) are built. See `docs/architecture.md` for the stage table.

## Prerequisites
Python 3.12 or newer and PyYAML. For the adapters: Playwright and Chromium (HTML rendering), Selenium and Chrome (live capture), Docker (flynn desk), Figma access through the Figma plugin, and a Canva sign-in where you use Canva.
