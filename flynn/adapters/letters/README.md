# Letters

flynn desk has two spaces, and each is a team of your account:

- **Designs:** product UI (the original `flynn` team).
- **Letters:** documents people read, such as release notes, reports, decks and newsletters.

The **Designs / Letters** switch sits at the top of the dashboard sidebar.

## A letter

**+ New letter** (in the Letters space) asks for a type and a title. It then:

1. creates the file in the Letters space;
2. opens a flynn run from the matching template in `adapters/html` (theme editorial);
3. opens the file. The first time it opens, the template is built on the canvas as editable layers.

| Type | Template | Canvas | Exports |
|---|---|---|---|
| Deck | `deck` | one 1280×720 board per slide, four per row | PDF, PowerPoint, Images, Canva |
| Newsletter | `newsletter` | one 640px board | Email draft, Images, PDF |
| Release note | `release-page` | one 1440px page | PDF, Images, Email draft |
| Report | `report` | one 1440px page | PDF, Images, PowerPoint |

**The page is the master; the canvas is where you edit.** In a letter:

- The toolbar's Record and Figma buttons step aside for **Export**.
- **Export** starts a flynn turn. flynn pulls your canvas edits (`push.py pull`), carries them into `page/index.html`, renders and checks the page, and writes the export to `<run>/exports/`. It never sends or publishes anything. PDFs go through the pdfsmith skill.
- **What flynn knows.** The flynn tab tells flynn it is in a letter: the type, the run and the template rules. The picker shows document themes, not product libraries.

## Files

- `letters.py`: `spaces`, `new --type --title`, `list`, `show`.
- State: `~/.flynn/spaces.json` (team ids) and `~/.flynn/letters/<file>.json` (type, title, run, theme, built).
- Overlay: `stack/flynn-overlay/www/spaces.js` (dashboard switch, New letter dialog, Export).
- Server: `/api/spaces`, `/api/letters/new`, `/api/letter/export`, and `letter_context` in `panel_server.py`.
- Deck slides: `pagetree.capture` returns each `.slide`'s box, and `push.split_slides` builds one board per slide.

## Note

The canvas connector serves one open file per account. With two flynn desk tabs open, flynn works in the first one.
