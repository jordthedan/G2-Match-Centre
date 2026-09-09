#!/usr/bin/env python3
"""Build a spoiler-free G2 Esports calendar with Liquipedia-first resilient fallbacks."""
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
UA = "G2MatchCentre/2.5 (+https://github.com/jordthedan/G2-Match-Centre)"
ADAPTER = "https://ics.snwfdhmp.com/matches.ics"
PRIMARY_SOURCES = {
    "Valorant": [
        "https://liquipedia.net/valorant/Liquipedia:Matches",
        "https://liquipedia.net/valorant/VCT/2026/Champions",
    ],
    "CS2": [
        "https://liquipedia.net/counterstrike/Liquipedia:Matches",
    ],
    "R6S": [
        "https://liquipedia.net/rainbowsix/Liquipedia:Matches",
        "https://liquipedia.net/rainbowsix/Europe_MENA_League/2026/Stage_2",
    ],
}
FALLBACK_ICS = {
    "Valorant": "https://raw.githubusercontent.com/snutij/esport_ics/main/ics/valorant/g2-esports.ics",
    "CS2": "https://raw.githubusercontent.com/snutij/esport_ics/main/ics/counter_strike/g2.ics",
    "R6S": "https://raw.githubusercontent.com/snutij/esport_ics/main/ics/rainbow_six_siege/g2-esports.ics",
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
    with urllib.request.urlopen(req, timeout=20) as r:
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


def summary_match_text(summary: str) -> str:
    # Provider feeds often prefix the actual matchup with a round label.
    return summary.rsplit(":", 1)[-1].strip()


def opponent_from_fields(summary: str, left: str, right: str) -> str:
    for team in (left, right):
        if team and not re.search(r"^G2(?: Esports)?$", team.strip(), re.I):
            return team.strip()
    text = summary_match_text(summary)
    m = re.search(r"(?:^|\b)G2(?: Esports)?\s+(?:vs\.?|v)\s+(.+)$", text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"^(.+?)\s+(?:vs\.?|v)\s+G2(?: Esports)?(?:\b|$)", text, re.I)
    return m.group(1).strip() if m else "TBD"


def is_main_g2(left: str, right: str, summary: str) -> bool:
    hay = " | ".join((left, right, summary))
    if re.search(r"\bG2[ ._-]*(?:Gozen|Ares|NORD|Hel|Oya)\b", hay, re.I):
        return False
    return bool(re.search(r"\bG2(?: Esports)?\b", hay, re.I))


def parse_ics_matches(game: str, text: str, source_url: str) -> list[Match]:
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
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    for e in events:
        summary = prop(e, "SUMMARY")
        left = prop(e, "X-LIQUIPEDIATOICAL-TEAMLEFTFULLNAME") or prop(e, "X-LIQUIPEDIATOICAL-TEAMLEFT")
        right = prop(e, "X-LIQUIPEDIATOICAL-TEAMRIGHTFULLNAME") or prop(e, "X-LIQUIPEDIATOICAL-TEAMRIGHT")
        if not is_main_g2(left, right, summary):
            continue
        start = parse_dt(prop(e, "DTSTART"))
        if not start or start < cutoff:
            continue
        description = prop(e, "DESCRIPTION")
        competition = prop(e, "X-LIQUIPEDIATOICAL-COMPETITION")
        if not competition and description:
            competition = description.split("\n", 1)[0].strip()
        out.append(Match(
            game=game,
            opponent=opponent_from_fields(summary, left, right),
            start=start,
            competition=competition,
            url=source_url,
            source_id=prop(e, "UID"),
        ))
    return dedupe(out)


def fetch_primary_page(game: str, lp_url: str) -> list[Match]:
    # Fetch broadly and filter G2 locally. Adapter-side team filters have been
    # observed returning empty feeds even while public G2 fixtures exist.
    query = urllib.parse.urlencode({
        "url": lp_url,
        "ignore_tbd": "false",
        "past_match_allow_seconds": "3600",
    })
    return parse_ics_matches(game, fetch(f"{ADAPTER}?{query}"), lp_url)


def fetch_game(game: str) -> tuple[list[Match], list[str]]:
    out: list[Match] = []
    failed: list[str] = []
    for lp_url in PRIMARY_SOURCES[game]:
        try:
            got = fetch_primary_page(game, lp_url)
            print(f"{game}: {len(got)} matches from Liquipedia source {lp_url}")
            out.extend(got)
        except Exception as exc:
            failed.append(lp_url)
            print(f"WARNING {game} Liquipedia source {lp_url}: {exc}")

    fallback_url = FALLBACK_ICS[game]
    try:
        got = parse_ics_matches(game, fetch(fallback_url), fallback_url)
        print(f"{game}: {len(got)} matches from fallback ICS")
        out.extend(got)
    except Exception as exc:
        failed.append(fallback_url)
        print(f"WARNING {game} fallback source {fallback_url}: {exc}")

    return dedupe(out), failed


def dedupe(items: list[Match]) -> list[Match]:
    seen, out = set(), []
    for m in sorted(items, key=lambda x: x.start):
        # A G2 roster cannot play two matches in the same game at the same instant;
        # using time here also collapses provider abbreviation/full-name differences.
        key = (m.game, m.start.replace(second=0, microsecond=0))
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid(m: Match) -> str:
    seed = f"{m.game}|{m.start.replace(second=0, microsecond=0).isoformat()}"
    return hashlib.sha1(seed.encode()).hexdigest() + "@g2-calendar"


def render(matches: list[Match]) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//G2 Match Centre//G2 Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:G2 Esports",
        "X-WR-TIMEZONE:Australia/Sydney", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for m in sorted(matches, key=lambda x: x.start):
        start = m.start.astimezone(UTC)
        duration = 2 if m.game == "R6S" else 3
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
    for game in PRIMARY_SOURCES:
        got, failed = fetch_game(game)
        print(f"{game}: {len(got)} unique future matches after merge")
        all_matches.extend(got)
        failures.extend(failed)

    all_matches = dedupe(all_matches)
    if not all_matches:
        raise SystemExit("No upcoming G2 matches found from primary or fallback sources; refusing to overwrite calendar.")

    OUT.write_text(render(all_matches), encoding="utf-8", newline="")
    print(f"Wrote {len(all_matches)} upcoming matches to {OUT}")
    if failures:
        print("Source warnings: " + ", ".join(failures))

if __name__ == "__main__":
    main()
