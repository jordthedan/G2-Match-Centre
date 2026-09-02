#!/usr/bin/env python3
"""Build a spoiler-free G2 Esports calendar from Liquipedia-backed match feeds."""
from __future__ import annotations

import hashlib
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
OUT = Path("g2-calendar.ics")
UA = "G2MatchCentre/2.1 (+https://github.com/jordthedan/G2-Match-Centre)"

# esports-ics reads Liquipedia through the MediaWiki API. The generic Matches pages
# have a deliberately short horizon, so each game also includes the currently active
# event page. This gives us the longer schedules Liquipedia already publishes while
# still keeping Liquipedia as the fixture source and avoiding HTML scraping.
ADAPTER = "https://ics.snwfdhmp.com/matches.ics"
SOURCES = {
    "Valorant": [
        "https://liquipedia.net/valorant/Liquipedia:Matches",
        "https://liquipedia.net/valorant/VCT/2026/Americas_League/Stage_2",
    ],
    "CS2": [
        "https://liquipedia.net/counterstrike/Liquipedia:Matches",
        "https://liquipedia.net/counterstrike/FISSURE/Playground/3",
    ],
    "Rainbow Six": [
        "https://liquipedia.net/rainbowsix/Liquipedia:Matches",
        "https://liquipedia.net/rainbowsix/Europe_MENA_League/2026/Stage_2",
    ],
    "League of Legends": [
        "https://liquipedia.net/leagueoflegends/Liquipedia:Matches",
        "https://liquipedia.net/leagueoflegends/LEC/2026/Summer",
    ],
}

@dataclass(frozen=True)
class Match:
    game: str
    opponent: str
    start: datetime
    competition: str = ""
    url: str = ""
    source_id: str = ""


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        if r.headers.get("Content-Encoding", "").lower() == "gzip":
            import gzip
            data = gzip.decompress(data)
        return data.decode("utf-8", "replace")


def unfold_ics(text: str) -> list[str]:
    raw = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    for line in raw:
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def unesc(value: str) -> str:
    return value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def prop(event: list[str], name: str) -> str:
    prefix = name.upper()
    for line in event:
        lhs, sep, rhs = line.partition(":")
        if sep and lhs.upper().split(";", 1)[0] == prefix:
            return unesc(rhs.strip())
    return ""


def parse_dt(value: str) -> datetime | None:
    value = value.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def opponent_from_fields(summary: str, left: str, right: str) -> str:
    for team in (left, right):
        if team and not re.search(r"^G2(?: Esports)?$", team.strip(), re.I):
            return team.strip()
    m = re.search(r"(?:^|\b)G2(?: Esports)?\s+(?:vs\.?|v)\s+([^—|\-]+)", summary, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"([^—|\-]+?)\s+(?:vs\.?|v)\s+G2(?: Esports)?(?:\b|$)", summary, re.I)
    return m.group(1).strip() if m else "TBD"


def is_main_g2(left: str, right: str, summary: str) -> bool:
    hay = " | ".join((left, right, summary))
    if re.search(r"\bG2\s+(?:Gozen|Ares|NORD|Hel|Oya)\b", hay, re.I):
        return False
    return bool(re.search(r"\bG2(?: Esports)?\b", hay, re.I))


def fetch_page(game: str, lp_url: str) -> list[Match]:
    query = urllib.parse.urlencode({
        "url": lp_url,
        "teams_regex": "^G2$|^G2 Esports$",
        "teams_regex_use_fullnames": "true",
        "ignore_tbd": "false",
        "past_match_allow_seconds": "21600",
    })
    text = fetch(f"{ADAPTER}?{query}")
    lines = unfold_ics(text)
    events: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None:
            current.append(line)

    out: list[Match] = []
    for e in events:
        summary = prop(e, "SUMMARY")
        left = prop(e, "X-LIQUIPEDIATOICAL-TEAMLEFTFULLNAME") or prop(e, "X-LIQUIPEDIATOICAL-TEAMLEFT")
        right = prop(e, "X-LIQUIPEDIATOICAL-TEAMRIGHTFULLNAME") or prop(e, "X-LIQUIPEDIATOICAL-TEAMRIGHT")
        if not is_main_g2(left, right, summary):
            continue
        start = parse_dt(prop(e, "DTSTART"))
        if not start or start < datetime.now(UTC) - timedelta(hours=8):
            continue
        out.append(Match(
            game=game,
            opponent=opponent_from_fields(summary, left, right),
            start=start,
            competition=prop(e, "X-LIQUIPEDIATOICAL-COMPETITION"),
            url=lp_url,
            source_id=prop(e, "UID"),
        ))
    return dedupe(out)


def fetch_game(game: str, urls: list[str]) -> tuple[list[Match], list[str]]:
    out: list[Match] = []
    failed: list[str] = []
    for lp_url in urls:
        try:
            got = fetch_page(game, lp_url)
            print(f"{game}: {len(got)} matches from {lp_url}")
            out.extend(got)
        except Exception as exc:
            failed.append(lp_url)
            print(f"WARNING {game} source {lp_url}: {exc}")
    return dedupe(out), failed


def dedupe(items: list[Match]) -> list[Match]:
    seen, out = set(), []
    for m in sorted(items, key=lambda x: x.start):
        key = (m.game, m.start.replace(second=0, microsecond=0), m.opponent.lower())
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid(m: Match) -> str:
    seed = m.source_id or f"{m.game}|{m.competition}|{m.start.isoformat()}"
    return hashlib.sha1(seed.encode()).hexdigest() + "@g2-calendar"


def render(matches: list[Match]) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//G2 Match Centre//Liquipedia G2 Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:G2 Esports",
        "X-WR-TIMEZONE:Australia/Sydney", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for m in sorted(matches, key=lambda x: x.start):
        start = m.start.astimezone(UTC)
        duration = 2 if m.game in {"Rainbow Six", "League of Legends"} else 3
        end = start + timedelta(hours=duration)
        title = f"G2 vs {m.opponent} — {m.game}"
        desc = m.competition
        if m.url:
            desc += ("\n" if desc else "") + "Source: " + m.url
        lines += [
            "BEGIN:VEVENT", f"UID:{uid(m)}", f"DTSTART:{start:%Y%m%dT%H%M%SZ}",
            f"DTEND:{end:%Y%m%dT%H%M%SZ}", f"SUMMARY:{esc(title)}",
            f"DESCRIPTION:{esc(desc)}", "STATUS:CONFIRMED", "TRANSP:OPAQUE",
            "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY",
            f"DESCRIPTION:{esc(title)} starts in 30 minutes", "END:VALARM", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    all_matches: list[Match] = []
    failures: list[str] = []
    for game, urls in SOURCES.items():
        got, failed = fetch_game(game, urls)
        print(f"{game}: {len(got)} unique future matches from Liquipedia")
        all_matches.extend(got)
        failures.extend(failed)

    all_matches = dedupe(all_matches)
    if not all_matches:
        raise SystemExit("No upcoming G2 matches found; refusing to overwrite calendar.")

    OUT.write_text(render(all_matches), encoding="utf-8", newline="")
    print(f"Wrote {len(all_matches)} upcoming matches to {OUT}")
    if failures:
        print("Temporary source failures: " + ", ".join(failures))

if __name__ == "__main__":
    main()
