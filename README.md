# G2 Match Centre — Live iPhone Calendar

A spoiler-free, auto-updating calendar for G2 Esports matches in exactly four games:

- Valorant
- Counter-Strike 2
- Rainbow Six Siege
- League of Legends

## Calendar behaviour

- Checks public esports schedules every hour with GitHub Actions.
- Publishes one combined `g2-calendar.ics` feed.
- Event titles use `G2 vs Opponent — Game` and never include results.
- Match times are stored as UTC so Apple Calendar converts them correctly to Australia/Sydney, including daylight-saving changes.
- Includes a 30-minute pre-match display alert in the feed.
- Playoff/bracket opponents can update when schedule sources publish the resolved matchup.
- The subscription is designed to keep rolling into future seasons without requiring a new calendar URL.

## Sources

The updater uses dedicated schedule sources for each esport rather than mixing unrelated G2 divisions:

- Valorant: VLR.gg G2 team schedule
- CS2: HLTV G2 match schedule
- Rainbow Six Siege: Ubisoft R6 Esports competition schedule
- League of Legends: Sheep Esports G2 team schedule

The sources are best-effort public pages. If a provider changes its page structure, that parser may need maintenance; the generator refuses to replace the calendar with an empty feed if all sources fail.

## iPhone subscription

Once GitHub Pages is enabled from the `main` branch `/ (root)`, subscribe to:

`https://jordthedan.github.io/G2-Match-Centre/g2-calendar.ics`

Do not download/import the file as a one-off calendar. Add the URL as a subscribed calendar so changes can refresh automatically.
