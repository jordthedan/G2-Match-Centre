#!/usr/bin/env python3
"""Build a spoiler-free G2 Esports iCalendar feed for Valorant, CS2, R6 Siege and LoL."""
from __future__ import annotations

import hashlib
import html as html_lib
import re
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")
UTC = timezone.utc
OUT = Path("g2-calendar.ics")
UA = "Mozilla/5.0 (compatible; G2Calendar/1.0; +https://github.com/jordthedan/G2-Match-Centre)"

@dataclass(frozen=True)
class Match:
    game: str
    opponent: str
    start: datetime
    competition: str = ""
    url: str = ""
    source_id: str = ""


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def textify(s: str) -> str:
    s = re.sub(r"<script\b[^>]*>.*?</script>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<style\b[^>]*>.*?</style>", " ", s, flags=re.I | re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html_lib.unescape(s)).strip()


def future(m: Match) -> bool:
    return m.start > datetime.now(UTC) - timedelta(hours=8)


def parse_vlr() -> list[Match]:
    url = "https://www.vlr.gg/team/11058/g2-esports"
    raw = fetch(url)
    out: list[Match] = []
    # VLR embeds authoritative Unix timestamps in data-ts attributes. Work from nearby match-card text.
    for hit in re.finditer(r'data-ts="(\d{10})"', raw):
        ts = int(hit.group(1))
        start = datetime.fromtimestamp(ts, UTC)
        if start < datetime.now(UTC) - timedelta(days=1):
            continue
        block = textify(raw[max(0, hit.start()-4500):hit.end()+4500])
        if "G2" not in block:
            continue
        teams = re.findall(r"\b(?:G2 Esports|LOUD|NRG|100 Thieves|Sentinels|FURIA|M80|ENVY|LEVIAT[ÁA]N|Cloud9|KRÜ|KRU|2GAME|BESTIA|TBD)\b", block, re.I)
        opp = next((t for t in teams if t.lower() not in {"g2", "g2 esports"}), "TBD")
        comp = "VCT"
        cm = re.search(r"(VCT[^|]{0,100})", block, re.I)
        if cm: comp = cm.group(1).strip()[:100]
        out.append(Match("Valorant", opp, start, comp, url, f"vlr-{ts}"))
    return dedupe(out)


def parse_hltv() -> list[Match]:
    url = "https://www.hltv.org/matches?team=5995"
    raw = fetch(url)
    out: list[Match] = []
    # HLTV match rows expose data-unix milliseconds. Keep only blocks containing G2.
    for hit in re.finditer(r'data-unix="(\d{13})"', raw):
        start = datetime.fromtimestamp(int(hit.group(1))/1000, UTC)
        if start < datetime.now(UTC) - timedelta(days=1):
            continue
        block = textify(raw[max(0, hit.start()-2500):hit.end()+5000])
        if not re.search(r"\bG2\b", block):
            continue
        # Team names generally surround the time in the match row; known fallback is TBD.
        names = re.findall(r"team(?:name)?[^A-Za-z0-9]{0,10}([A-Za-z0-9 .'-]{2,30})", block, re.I)
        opp = next((n.strip() for n in names if "G2" not in n), "TBD")
        comp = "Counter-Strike 2"
        for marker in ("BLAST", "FISSURE", "ESL", "PGL", "IEM"):
            p = block.find(marker)
            if p >= 0:
                comp = block[p:p+90].strip()
                break
        out.append(Match("CS2", opp, start, comp, url, f"hltv-{int(hit.group(1))}"))
    return dedupe(out)


def parse_siege() -> list[Match]:
    url = "https://www.ubisoft.com/en-gb/esports/rainbow-six/siege/competition/508/15004"
    raw = fetch(url)
    txt = textify(raw)
    out: list[Match] = []
    # Ubisoft's rendered schedule is Europe/MENA local time (CEST during Sep/Oct 2026).
    months = {"September": 9, "October": 10, "November": 11, "December": 12}
    day_re = re.compile(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2})\s+(September|October|November|December)", re.I)
    days = list(day_re.finditer(txt))
    paris = ZoneInfo("Europe/Paris")
    for i, d in enumerate(days):
        segment = txt[d.end(): days[i+1].start() if i+1 < len(days) else min(len(txt), d.end()+2500)]
        day, month = int(d.group(1)), months[d.group(2).title()]
        for mm in re.finditer(r"(\d{1,2}:\d{2})\s+([A-Za-z0-9 .'-]+?)-:-.*?Upcoming\s+([A-Za-z0-9 .'-]+?)(?=\s+Match details|\s+\d{1,2}:\d{2}|$)", segment, re.I):
            home, away = mm.group(2).strip(), mm.group(3).strip()
            if "g2 esports" not in (home+" "+away).lower():
                continue
            opp = away if "g2 esports" in home.lower() else home
            local = datetime(2026, month, day, *map(int, mm.group(1).split(":")), tzinfo=paris)
            out.append(Match("Rainbow Six", opp, local.astimezone(UTC), "Europe MENA League Stage 2", url, f"r6-{day}-{month}-{mm.group(1)}-{opp}"))
    return dedupe(out)


def parse_lol() -> list[Match]:
    # Sheep exposes a compact G2 team page and uses UTC times in its upcoming-match listing.
    url = "https://www.sheepesports.com/en/lol/teams/G2%2520Esports"
    txt = textify(fetch(url))
    out: list[Match] = []
    now = datetime.now(UTC)
    months = {m:i for i,m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
    # Example rendered form: LEC Bo5 Sep 5, 10:00 G2 Esports vs Team Vitality
    pat = re.compile(r"([A-Za-z0-9 .'-]{2,40})\s+Bo\d\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{1,2}:\d{2})\s+(.{0,80}?G2 Esports.{0,80}?)(?=\s+(?:LEC|LCK|LPL|LTA|Worlds|MSI|$))", re.I)
    for m in pat.finditer(txt):
        year = now.year
        mon, day = months[m.group(2).title()], int(m.group(3))
        hh, mi = map(int, m.group(4).split(":"))
        start = datetime(year, mon, day, hh, mi, tzinfo=UTC)
        if start < now - timedelta(days=30): start = start.replace(year=year+1)
        tail = m.group(5)
        vm = re.search(r"G2 Esports\s+vs\s+([A-Za-z0-9 .'-]+)", tail, re.I)
        if not vm:
            vm = re.search(r"([A-Za-z0-9 .'-]+)\s+vs\s+G2 Esports", tail, re.I)
        opp = vm.group(1).strip() if vm else "TBD"
        out.append(Match("League of Legends", opp, start, m.group(1).strip(), url, f"lol-{start.isoformat()}-{opp}"))
    return dedupe(out)


def dedupe(items: list[Match]) -> list[Match]:
    seen, out = set(), []
    for m in sorted(items, key=lambda x: x.start):
        key = (m.game, m.start.replace(second=0, microsecond=0), m.opponent.lower())
        if key not in seen:
            seen.add(key); out.append(m)
    return out


def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid(m: Match) -> str:
    raw = f"{m.game}|{m.source_id or m.url}|{m.start.isoformat()}|{m.opponent}".encode()
    return hashlib.sha1(raw).hexdigest() + "@g2-calendar"


def render(matches: list[Match]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//G2 Match Centre//G2 Calendar//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:G2 Esports", "X-WR-TIMEZONE:Australia/Sydney", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for m in sorted(matches, key=lambda x: x.start):
        start = m.start.astimezone(UTC); end = start + timedelta(hours=3)
        title = f"G2 vs {m.opponent} — {m.game}"
        desc = m.competition
        if m.url: desc += ("\\n" if desc else "") + m.url
        lines += ["BEGIN:VEVENT", f"UID:{uid(m)}", f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}", f"SUMMARY:{esc(title)}", f"DESCRIPTION:{esc(desc)}", "STATUS:CONFIRMED", "TRANSP:OPAQUE", "BEGIN:VALARM", "TRIGGER:-PT30M", "ACTION:DISPLAY", f"DESCRIPTION:{esc(title)} starts in 30 minutes", "END:VALARM", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    parsers = [parse_vlr, parse_hltv, parse_siege, parse_lol]
    all_matches: list[Match] = []
    for parser in parsers:
        try:
            got = [m for m in parser() if future(m)]
            print(f"{parser.__name__}: {len(got)} future matches")
            all_matches.extend(got)
        except Exception as e:
            print(f"WARNING {parser.__name__}: {e}")
    all_matches = dedupe(all_matches)
    if not all_matches:
        raise SystemExit("No upcoming G2 matches found; refusing to overwrite calendar.")
    OUT.write_text(render(all_matches), encoding="utf-8", newline="")
    print(f"Wrote {len(all_matches)} upcoming matches to {OUT}")

if __name__ == "__main__":
    main()
