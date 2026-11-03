# Figma import

The Figma button (next to Record in flynn desk's toolbar) imports a whole Figma page as real, editable layers, on a new page in the open file.

1. **Paste the link.** In Figma, right-click the page in the Pages list and choose Copy link. The server only accepts `figma.com/design|file/...?node-id=...` links. You can also name the new page; it defaults to "Imported · <Figma page name>".
2. **flynn reads the page.** Through your own Figma connection, with read-only tools, flynn:
   - checks the link is a page (type CANVAS) your account can open, and stops with a plain message if it isn't;
   - writes each top-level frame as a standalone HTML page (real text, exact sizes, colours and fonts, icons as SVG), renders it and compares it with Figma's screenshot;
   - lists the frames with their Figma positions in `frames.json`.
3. **The page is built.** `import_page.py` captures every frame with the converter that builds flynn's designs and builds it in flynn desk at its Figma position.

```bash
python3 adapters/figma/import_page.py <run folder> [--page-name NAME] [--file FILE_ID]
```

**Not imported:** components and variants, auto layout, prototype links and comments. Fonts missing from flynn desk fall back to its default font, and flynn says so.
