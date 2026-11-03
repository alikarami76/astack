"""Slash commands in the flynn tab (type / in the message field), one list for every client and engine.

Each command is one of three kinds:
  ui      the tab does it itself (opens a popup, the history, a picker)
  server  the panel server does it (sync libraries, new conversation, context, usage, settings)
  prompt  a brief for flynn, built here from the command and what the user typed after it

`space` limits a command to Designs or Letters files ("" = both).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Command:
    name: str
    label: str
    hint: str
    kind: str                  # ui | server | prompt
    arg: str = ""              # placeholder for what follows the command; empty = no argument
    space: str = ""            # designs | letters | ""
    group: str = "flynn"


COMMANDS = [
    # thinking
    Command("brainstorm", "Brainstorm with a design lead", "flynn finds the real need, challenges assumptions, helps you decide, then builds it", "prompt", "the idea or problem", group="Think"),
    # canvas and design systems
    Command("record", "Record a product", "Capture a live product's screens and design system into this file", "ui", space="designs", group="Canvas"),
    Command("figma", "Import from Figma", "Rebuild a Figma page here as editable layers", "ui", "page link (optional)", "designs", "Canvas"),
    Command("prototype", "Make it clickable", "flynn links the screens on a page into a prototype you can play", "prompt", "page name (optional)", group="Canvas"),
    Command("pull", "Carry my edits back", "flynn reads your canvas edits of a run back into its page", "prompt", "run (optional)", group="Canvas"),
    Command("use", "Design from a library", "Choose the design system flynn designs from", "ui", "library", group="Design systems"),
    Command("sync", "Update libraries", "Bring every design system library up to date with the store", "server", group="Design systems"),
    Command("library", "Create a library", "Make a new design system library (from the store, a recording or a description)", "prompt", "name and source", group="Design systems"),
    Command("ds", "Change a design system", "flynn proposes a token or component change for you to approve", "prompt", "what changed", group="Design systems"),
    # letters
    Command("chart", "Build a chart", "A data visualization for this letter, from numbers you give or clu fetches", "prompt", "what to show", "letters", "Letters"),
    Command("export", "Export", "PDF, PowerPoint, images, email or Canva for this letter", "ui", space="letters", group="Letters"),
    # teammates
    Command("clu", "Ask clu", "Bring in clu for an analysis or to fetch data", "prompt", "question", group="Teammates"),
    Command("quorra", "Build with quorra", "Spin up quorra to build this in code (its own work clone; nothing ships without you)", "prompt", "what to build", group="Teammates"),
    Command("skill", "Create a skill", "flynn drafts a new flynn skill for you to approve", "prompt", "what it should do", group="Teammates"),
    Command("studio", "Put it in Studio", "Open this conversation's designs in flynn Studio", "prompt", group="Teammates"),
    # conversation
    Command("new", "New conversation", "Start fresh; this one stays in history", "server", group="Conversation"),
    Command("history", "Conversations", "Open a past conversation", "ui", group="Conversation"),
    Command("attach", "Attach files", "Add images, PDFs, CSV or other files to your message", "ui", group="Conversation"),
    Command("context", "Context", "What flynn sees with your next message", "server", group="Conversation"),
    Command("usage", "Usage", "What this conversation and today have cost", "server", group="Conversation"),
    Command("settings", "Settings", "Which AI runs flynn", "server", group="Conversation"),
    Command("stop", "Stop flynn", "Stop the current turn", "server", group="Conversation"),
    Command("help", "Commands", "Every command", "ui", group="Conversation"),
]
BY_NAME = {c.name: c for c in COMMANDS}


def listing(space: str) -> list[dict]:
    return [asdict(c) for c in COMMANDS if not c.space or c.space == space]


def prompt(name: str, arg: str, ctx: dict) -> tuple[str, str]:
    """(the brief for flynn, the text shown in the chat) for a prompt command."""
    arg = (arg or "").strip()
    shown = f"/{name}" + (f" {arg}" if arg else "")
    letter = ctx.get("letter") or {}
    if name == "brainstorm":
        return ("Use the flynn-brainstorm skill: act as the design lead for this" + (f": {arg}" if arg else " (ask me what we are solving)")
                + ". Find the need, challenge the risky assumptions, give me genuinely different directions with your "
                "recommendation, help me decide, then build it through the design contract. Ask with ask_user, one "
                "question at a time.", shown)
    if name == "prototype":
        where = f"the page \"{arg}\"" if arg else "the page I'm on"
        return (f"Make the screens on {where} clickable as a prototype. Call prototype_outline, decide what every "
                "primary action, link, tab, back and close should do (go to a screen, open a pop-up, close it, go back), "
                "choose the first screen and name the flow, then call prototype_propose. Use slide animations for forward "
                "and back navigation and dissolve for pop-ups. Tell me in two lines what I can try once I approve; "
                "do not ask questions unless two screens could equally be the target.", shown)
    if name == "pull":
        return (f"Read my canvas edits back with canvas_pull for {('runs/' + arg) if arg else 'the latest run in this conversation'} "
                "and tell me which boards I edited and what changed. Carry them into the page.", shown)
    if name == "library":
        return ("Create a new design system library in flynn desk" + (f": {arg}" if arg else "") + ". If the system is already "
                "in the store, make its canvas library with adapters/library/library.py. If it is a live product, tell me to "
                "use /record. If it is a description, draft the tokens and components as a design system change "
                "(propose_ds_change) for me to approve first. Ask me with ask_user if the source is unclear.", shown)
    if name == "ds":
        return ("I want to change the design system: " + (arg or "(ask me what)") + ". Check the store, then propose the "
                "change with propose_ds_change (old and new values); do not apply anything yourself.", shown)
    if name == "chart":
        return ("Build a data visualization for this letter" + (f" ({letter.get('type')}, run {letter.get('run')})" if letter else "")
                + (f": {arg}." if arg else ".") + " Follow skills/flynn-core/references/dataviz.md and use adapters/html/charts.py "
                "(or chartspec.py). If the numbers are not in my message or the run, bring in clu (Agent, subagent_type clu, "
                "teammate mode) to fetch and verify them, and design only numbers clu verified. Put the chart into the "
                "letter's page as a figure and say where.", shown)
    if name == "clu":
        return ("Bring in clu (Agent tool, subagent_type \"clu\", teammate mode) for this: " + (arg or "(ask me what)") + ". "
                "Relay clu's NEEDS_ALIGNMENT questions to me with ask_user. When clu is done, give me its answer in plain "
                "words with the numbers it verified and its run folder.", shown)
    if name == "quorra":
        return ("Bring in quorra (Agent tool, subagent_type \"quorra\", teammate mode) to build this: " + (arg or "(ask me what)")
                + ". Give quorra the design (run folder, the picked option, the TSX export if there is one). quorra builds in its "
                "own work clone; it must not push, open a merge request or touch my clones without my approval. Relay its "
                "questions to me with ask_user, and report what it built, where, and what needs my approval.", shown)
    if name == "skill":
        return ("Create a new flynn skill: " + (arg or "(ask me what it should do)") + ". Read skills/flynn-core/SKILL.md "
                "and one or two other skills in skills/ for the format. Keep it focused: a description that says exactly "
                "when to use it, then numbered steps, the rules, and an example. Use flynn's real scripts and paths, and "
                "plain words. Ask me with ask_user if the trigger or scope is unclear. Then call propose_skill; do not "
                "write into skills/ yourself.", shown)
    if name == "studio":
        return ("Put this conversation's designs in flynn Studio: start Studio if it is not running (python3 "
                "adapters/studio/studio.py serve, in the background), make sure each run is there, and give me the links.", shown)
    raise KeyError(name)
