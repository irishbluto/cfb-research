# Fonts for generate_team_cards.py

The card generator looks for these font files here. Until you drop them
in, the script falls back to system serif fonts (Liberation / DejaVu /
Georgia) and you'll get a heavier-but-less-distinctive team name.

## Required

### `BebasNeue-Regular.ttf`

Tall condensed sans, the team-name display font. Bebas Neue only ships
in one weight — that's normal; the name "Regular" is misleading because
it's already heavy by design.

**License:** SIL Open Font License 1.1 (free to redistribute).

**Where to grab it:**

- Google Fonts: https://fonts.google.com/specimen/Bebas+Neue
  - Click "Get font" → "Download all" → unzip → pick `BebasNeue-Regular.ttf`
- Or direct from the GitHub repo: https://github.com/dharmatype/Bebas-Neue

Drop the `.ttf` directly into this folder (`cfb-research/scripts/fonts/`)
and commit. The VPS will pick it up on next `git pull`.

## Adding more display fonts later

If you decide to test other display fonts (Playfair Display Black,
Anton, Oswald, etc.), drop the `.ttf` here and update the
`FONT_CANDIDATES["display_team"]` list at the top of
`generate_team_cards.py` to point at the new file.
