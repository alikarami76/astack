# Design system libraries on the canvas

Each design system flynn knows becomes a shared library file on the canvas. These files live in the "flynn libraries" project and are listed under **Libraries**, so you or flynn can connect them to any file. The flynn store stays the source of truth; a sync brings the library file up to date.

## Commands

```bash
python3 adapters/library/library.py list
python3 adapters/library/library.py sync acme-app                  # tokens and components
python3 adapters/library/library.py sync acme-app --only tokens
python3 adapters/library/library.py sync acme-app --only components --force   # rebuild components (new ids)
python3 adapters/library/builder.py status | close
```

## What a library holds

- **Colours:** one library colour per colour token. Base colours go under `Palette/<family>`, and semantic colours, resolved to their values, go under `Semantic/<group>`.
- **Typographies:** one per type style. Each is set in the system's font (Vazirmatn by default) at its desktop size, in a normal and a compact (`-c`) line height.
- **Token set:** one set per system, holding colours (semantic colours point to their base colours), spacing, radii, sizes, font weights and the font family. Token names use the first word as a group (`color.button-primary-hover`), because Penpot does not let a token also be a group.
- **Components page:**
  - Every component sample (from a recording's review, or from a kit of real markup and CSS) is drawn with the same converter that builds flynn's designs.
  - Each sample becomes a library component, and samples sharing a name become one variant set. Variant values are read off the product's modifier classes (for example `neutralOutlined · primary · medium`).
- **Ownership:** every item flynn makes is tagged (shared plugin data `flynn/key`). A sync updates its own items in place and removes only its own items that have left the store.

## How systems change over time

Every change goes through `changes.py`. flynn proposes, you approve in the flynn tab, then the store and the library update:

- **Operations:** `token.set`, `token.remove`, `component.set` (description, variants, states, usage and the like), `component.remove`, `component.sample` (new HTML markup for a sample, kept in `systems/<system>/library-samples/` and picked up by the library), and `recording.apply` (rebuild from a recording's review).
- **The "before" values:** a proposal records the current values, so the card can show old → new.
- **Tiers:** new rows enter as `proposed` (`inferred` for recorded systems), and updates keep their tier.
- **Files and log:** changes live in `~/.flynn/ds-changes/`; the log is `~/.flynn/logs/ds-changes.log`.

## Using a library in other files

`push.py` handles this whenever a design is built on the canvas, from the flynn tab or from Studio:

1. **Linking.** It links the file to the library on the backend (the plugin's own connect call does not save the link) and loads the library into the open file. If the open file can't see the library yet, the build warns you to reload the file once.
2. **Choosing elements.** An element tagged `data-uikit` becomes a real instance only when it is self-contained (no other library components inside, not container-sized) and a library variant matches it exactly: the same fill, the same border colour, and the same number of texts and icons. Fidelity comes first, so everything else is drawn as shapes, as before.
3. **Placing the text.** The instance is sized to the element, and the page's texts go into the instance's texts, keeping their exact boxes.

## Copies in org teams

A canvas file can only use libraries from its own team (Penpot refuses the link otherwise). So every team in a flynn organization (`adapters/orgs`) gets its own copy of each library, in that team's "flynn libraries" project:

- **One sync for all.** `library.py sync <system>` updates the original and then every copy. `--team <id>` syncs one copy only.
- **Separate maps.** Each copy keeps its own state (`~/.flynn/libraries/<system>@<team>.json`), so its components are updated in place like the original's.
- **The right copy for each build.** A build links the copy in the open file's team. A file in a team outside any org can only use the original if it is in the flynn team. Otherwise the build warns that the team needs to join an org.
- **Listing.** `library.py list` shows each copy under its library.

## Why there is a builder account

The canvas connector keeps one connection per account, and your tab holds the `flynn` account's connection. `builder@flynn.local` (set up with the user's approval) is an editor in the flynn team with its own connector key. Its hidden Chrome (`~/.flynn/builder-chrome`, port 9224) opens the library file during a sync, and its edits reach your files the way a collaborator's do. Your tab is never touched. The password and key are in the keychain (`builder-password`, `builder-mcp-key`). Log: `~/.flynn/logs/builder.log`.

## Keeping libraries current

- **Tokens:** updated in place on every sync.
- **Components:** updated in place, so their ids never change and instances in other files stay linked:
  - Each sample has a fingerprint. A changed sample is redrawn inside its existing main component.
  - A group that gains or loses samples is rebuilt, and only that group gets new ids.
  - A component gone from the source is removed. Unchanged samples are left alone.
  - When the source is unchanged (the kit, or a recorded system's `mine.json`/`review.json`), the sync skips components altogether. `--force` rebuilds everything, with new ids.
- **Recorded systems (`rec:<system>`):** the samples are the real recorded elements, named by flynn's review (see adapters/recorder/README.md).
- **Sync button:** the library icon in the flynn tab header syncs every library. Progress shows in the chat.

`state` is local: `~/.flynn/libraries/<system>.json` holds the library file and id, the component map (id, variant, size and signature) and the kit hash.
