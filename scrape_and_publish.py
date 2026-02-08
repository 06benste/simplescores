#!/usr/bin/env python3
"""
Obtain scores and tables from Sky Sports, update HTML files, and push to GitHub.
Run from project root. Configure GitHub Pages to serve from /docs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Use UK time for "today" so scores match the day users expect (e.g. Sunday in UK = Sunday's games)
def _today_uk() -> date:
    return datetime.now(ZoneInfo("Europe/London")).date()

import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "templates"
DOCS_DIR = ROOT / "docs"
LEAGUE_NAMES = {
    "premier_league": "Premier League",
    "championship": "Championship",
    "league_one": "League One",
    "league_two": "League Two",
    "fa_cup": "FA Cup",
    "carabao_cup": "Carabao Cup",
}

LEAGUES = {
    "premier_league": {
        "fixtures_url": "https://www.skysports.com/premier-league-fixtures",
        "results_url": "https://www.skysports.com/premier-league-results",
        "table_url": "https://www.skysports.com/premier-league-table",
    },
    "championship": {
        "fixtures_url": "https://www.skysports.com/championship-fixtures",
        "results_url": "https://www.skysports.com/championship-results",
        "table_url": "https://www.skysports.com/championship-table",
    },
    "league_one": {
        "fixtures_url": "https://www.skysports.com/league-1-fixtures",
        "results_url": "https://www.skysports.com/league-1-results",
        "table_url": "https://www.skysports.com/league-1-table",
    },
    "league_two": {
        "fixtures_url": "https://www.skysports.com/league-2-fixtures",
        "results_url": "https://www.skysports.com/league-2-results",
        "table_url": "https://www.skysports.com/league-2-table",
    },
    "fa_cup": {
        "fixtures_url": "https://www.skysports.com/fa-cup-fixtures",
        "results_url": "https://www.skysports.com/fa-cup-results",
        "table_url": None,
    },
    "carabao_cup": {
        "fixtures_url": "https://www.skysports.com/carabao-cup-fixtures",
        "results_url": "https://www.skysports.com/carabao-cup-results",
        "table_url": None,
    },
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# -----------------------------------------------------------------------------
# Scraping (adapted from simplepl)
# -----------------------------------------------------------------------------


def _parse_date_header(date_text: str, today: date) -> bool:
    """Parse date header in format 'Thursday 1st January' and check if it matches today."""
    if not date_text:
        return False
    
    date_text = date_text.strip()
    date_lower = date_text.lower()
    
    # Check for "yesterday" - exclude these
    if "yesterday" in date_lower:
        return False
    
    # Parse format: "Thursday 1st January" or "Thursday 1 January"
    today_day = today.strftime("%A").lower()
    today_date_str = today.strftime("%d").lstrip("0")  # "25" or "5"
    today_month = today.strftime("%B").lower()  # "january"
    
    # Check exact format: "DayName DayNumber[st/nd/rd/th] MonthName"
    # Pattern: day name, optional whitespace, day number with optional ordinal, optional whitespace, month name
    pattern = rf'^{re.escape(today_day)}\s+{today_date_str}(?:st|nd|rd|th)?\s+{re.escape(today_month)}$'
    if re.match(pattern, date_lower):
        return True
    
    # Also check without ordinal: "Thursday 1 January"
    pattern_alt = rf'^{re.escape(today_day)}\s+{today_date_str}\s+{re.escape(today_month)}$'
    if re.match(pattern_alt, date_lower):
        return True
    
    return False


def _extract_date_from_context(context: str, today: date) -> bool:
    """Check if context contains today's date indicators."""
    context_lower = context.lower()
    today_day = today.strftime("%A").lower()
    today_date_str = today.strftime("%d").lstrip("0")  # "25" or "5"
    today_date_padded = today.strftime("%d")  # "25" or "05"
    today_month = today.strftime("%B").lower()  # "january"
    today_month_short = today.strftime("%b").lower()  # "jan"
    today_month_num = today.strftime("%m")  # "01"
    today_year = today.strftime("%Y")  # "2026"
    today_year_short = today.strftime("%y")  # "26"
    
    # Check for "yesterday" - exclude these
    if re.search(r'\byesterday\b', context_lower):
        return False
    
    # Check for explicit past dates (yesterday)
    yesterday = date.fromordinal(today.toordinal() - 1)
    yesterday_day = yesterday.strftime("%A").lower()
    yesterday_date_str = yesterday.strftime("%d").lstrip("0")
    if re.search(rf'\b{yesterday_day}\b', context_lower):
        return False
    if re.search(rf'\b{yesterday_date_str}\s+{yesterday.strftime("%B").lower()}\b', context_lower):
        return False
    
    # Check for today's day name (with word boundaries)
    day_pattern = r'\b' + re.escape(today_day) + r'\b'
    if re.search(day_pattern, context_lower):
        return True
    
    # Check for today's date in various formats
    date_patterns = [
        rf'\b{today_date_str}\s+{today_month}\b',  # "25 january"
        rf'\b{today_date_padded}\s+{today_month}\b',  # "25 january" (with leading zero)
        rf'\b{today_month_short}\s+{today_date_str}\b',  # "jan 25"
        rf'\b{today_month_short}\s+{today_date_padded}\b',  # "jan 25" (with leading zero)
        rf'\b{today_date_str}(?:st|nd|rd|th)?\s+{today_month}\b',  # "25th january"
        rf'\b{today_date_str}[/-]{today_month_num}[/-]{today_year}\b',  # "25/01/2026" or "25-01-2026"
        rf'\b{today_date_padded}[/-]{today_month_num}[/-]{today_year}\b',  # "25/01/2026" (padded)
        rf'\b{today_date_str}[/-]{today_month_num}[/-]{today_year_short}\b',  # "25/01/26"
        rf'\b{today_date_padded}[/-]{today_month_num}[/-]{today_year_short}\b',  # "25/01/26" (padded)
    ]
    for pattern in date_patterns:
        if re.search(pattern, context_lower):
            return True
    
    # Check for "today" keyword
    if re.search(r'\btoday\b', context_lower):
        return True
    
    return False


def _is_today_match(match: dict, match_text: str = "", context: str = "", page_text: str = "", match_position: int = 0) -> bool:
    # Always include live matches
    if match.get("status") in ("LIVE", "HT"):
        return True
    
    text = (match_text or match.get("text") or "").lower()
    full_context = (context or "").lower() + " " + text
    today = _today_uk()
    today_day = today.strftime("%A").lower()
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    # Check for "yesterday" - always exclude
    if "yesterday" in full_context or (page_text and "yesterday" in page_text[max(0, match_position - 1000):match_position + 1000].lower()):
        return False

    # Check if context/text explicitly mentions a different day (excluding today)
    for d in day_names:
        if d != today_day:
            pattern = r'\b' + re.escape(d) + r'\b'
            if re.search(pattern, full_context):
                return False
    
    # Check if we can find today's date in the context
    has_today_date = _extract_date_from_context(full_context, today)
    
    # Also check in a larger page context for section headers
    has_today_in_page = False
    if page_text and match_position > 0:
        # Look backwards for "today" or date headers
        page_section = page_text[max(0, match_position - 2000):match_position + 500].lower()
        has_today_in_page = _extract_date_from_context(page_section, today)
        # Check for "yesterday" in page section
        if "yesterday" in page_section:
            return False
    
    has_today_anywhere = has_today_date or has_today_in_page
    
    # For fixtures, include if:
    # 1. Today's date is found in context, OR
    # 2. Has a time and no other day is mentioned (assumed to be today)
    if match.get("status") == "FIXTURE":
        if has_today_anywhere:
            return True
        if match.get("date_time") and re.match(r"\d{1,2}:\d{2}[ap]m", match.get("date_time", "")):
            return True
        return False

    # For finished matches: include if we find today's date, or if match appears early in results
    # (Sky Sports typically shows today's matches first)
    if match.get("status") == "FT":
        # If we found today's date anywhere, include
        if has_today_anywhere:
            return True
        
        # If match appears in first 30% of page and no conflicting dates, likely today's
        # This is a heuristic: today's matches typically appear before yesterday's
        if page_text and match_position > 0:
            page_length = len(page_text)
            if page_length > 0 and (match_position / page_length) < 0.3:
                # Early in page, no yesterday found, likely today
                return True
        
        # Otherwise exclude to be safe
        return False
    
    # For other statuses (AET, etc.), only include if today's date is found
    return has_today_anywhere


def _clean_team(words: list[str], take_last: bool, max_words: int = 3) -> str:
    kept = [w for w in words if w and w[0].isupper() and w.isalpha()]
    if not kept:
        return ""
    slice_ = kept[-max_words:] if take_last else kept[:max_words]
    return " ".join(slice_)[:30]


def _match_has_score_or_live(m: dict) -> bool:
    """True if match has a score or is live/HT/FT (i.e. from results page)."""
    if m.get("status") in ("LIVE", "HT", "FT"):
        return True
    if (m.get("score1") or "") != "" or (m.get("score2") or "") != "":
        return True
    return False


def _extract_today_matches_from_soup(soup: "BeautifulSoup", today: date, today_str: str, today_str_alt: str) -> list[dict]:
    """Parse a fixtures or results page; return list of today's match dicts."""
    import json
    matches = []
    seen = set()
    match_sections = soup.find_all("div", class_="ui-tournament-matches")

    for match_section in match_sections:
        date_header = None
        current = match_section.find_previous()
        while current:
            if current.name == "div":
                classes = current.get("class", [])
                if isinstance(classes, list) and any("ui-sitewide-component-header__wrapper--h3" in str(c) for c in classes):
                    date_header = current
                    break
            if current.name == "div" and "ui-tournament-matches" in current.get("class", []):
                break
            current = current.find_previous()

        section_date = ""
        if date_header:
            date_span = date_header.find("span", {"data-role": "short-text-target"})
            if date_span:
                section_date = date_span.get_text(strip=True)
            else:
                header_text = date_header.get_text(strip=True)
                if header_text:
                    section_date = header_text
        if not section_date:
            continue
        if not _parse_date_header(section_date, today):
            continue

        match_items = match_section.find_all("div", class_="ui-sport-match-score")
        for match_item in match_items:
            data_state = match_item.get("data-state")
            if not data_state:
                continue
            try:
                match_data = json.loads(data_state)
            except json.JSONDecodeError:
                continue
            match_date = match_data.get("start", {}).get("date", "")
            if match_date:
                match_is_today = (
                    match_date.lower() == today_str.lower()
                    or match_date.lower() == today_str_alt.lower()
                    or _parse_date_header(match_date, today)
                    or _extract_date_from_context(match_date.lower(), today)
                )
                if not match_is_today:
                    continue
            home_team = match_data.get("teams", {}).get("home", {}).get("name", {}).get("full", "")
            away_team = match_data.get("teams", {}).get("away", {}).get("name", {}).get("full", "")
            if not home_team or not away_team:
                continue
            home_score = match_data.get("teams", {}).get("home", {}).get("score", {}).get("current")
            away_score = match_data.get("teams", {}).get("away", {}).get("score", {}).get("current")
            match_state = match_data.get("matchState", "").lower()
            is_result = match_data.get("isResult", False)
            is_in_play = match_data.get("isInPlay", False)
            currently_playing = match_data.get("currentlyPlaying", False)
            status = "FIXTURE"
            if currently_playing or is_in_play:
                status = "LIVE"
            elif match_state == "ht" or "half time" in match_data.get("statusDescription", {}).get("full", "").lower():
                status = "HT"
            elif is_result or match_state == "ft":
                status = "FT"
            time_12hr = match_data.get("start", {}).get("time12hr", "")
            date_time = time_12hr if time_12hr else ""
            key = f"{home_team}_{home_score}_{away_team}_{away_score}"
            if key not in seen:
                seen.add(key)
                matches.append({
                    "team1": home_team,
                    "team2": away_team,
                    "score1": str(home_score) if home_score is not None else "",
                    "score2": str(away_score) if away_score is not None else "",
                    "status": status,
                    "date_time": date_time,
                    "text": f"{home_team} {home_score if home_score is not None else ''} - {away_score if away_score is not None else ''} {away_team}",
                })

    if not matches:
        all_text = soup.get_text(separator=" ", strip=True)
        for m in re.finditer(
            r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s+(\d+)\s*[-–—]\s*(\d+)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)",
            all_text,
        ):
            t1_full, s1, s2, t2_full = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
            t1 = _clean_team(t1_full.split(), True)
            t2 = _clean_team(t2_full.split(), False)
            key = f"{t1}_{s1}_{t2}_{s2}"
            if key not in seen and t1 and t2 and len(t1) > 2 and len(t2) > 2:
                seen.add(key)
                matches.append({
                    "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                    "status": "FT", "date_time": "",
                    "text": f"{t1_full} {s1} - {s2} {t2_full}",
                })
    return matches


def scrape_scores() -> dict:
    """Scrape today's matches from both fixtures and results pages, then merge.
    Fixtures page has upcoming games (with kick-off times); results page has live/finished.
    Using both gives a complete picture for the day."""
    out = {}
    today = _today_uk()
    day_name = today.strftime("%A")
    day_num = today.day
    month_name = today.strftime("%B")
    if 10 <= day_num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    today_str = f"{day_name} {day_num}{suffix} {month_name}"
    today_str_alt = f"{day_name} {day_num} {month_name}"

    for league_name, urls in LEAGUES.items():
        try:
            # Fetch both fixtures (upcoming) and results (live/finished) for today
            matches_by_key = {}  # (team1, team2) -> match
            for url_key in ("fixtures_url", "results_url"):
                url = urls.get(url_key)
                if not url:
                    continue
                r = requests.get(url, timeout=10, headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
                soup = BeautifulSoup(r.content, "html.parser")
                for match in _extract_today_matches_from_soup(soup, today, today_str, today_str_alt):
                    key = (match["team1"], match["team2"])
                    existing = matches_by_key.get(key)
                    if existing is None:
                        matches_by_key[key] = match
                    elif _match_has_score_or_live(match) and not _match_has_score_or_live(existing):
                        # Prefer result (score/live) over fixture-only
                        matches_by_key[key] = match
                    # else keep existing (e.g. already have result)

            # Sort by kick-off time then team names
            matches = sorted(
                matches_by_key.values(),
                key=lambda m: (m.get("date_time") or "", m["team1"], m["team2"]),
            )
            out[league_name] = {"matches": matches, "last_updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"Error scraping {league_name} scores: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            out[league_name] = {"matches": [], "last_updated": datetime.now().isoformat(), "error": str(e)}
    return out


def scrape_tables() -> dict:
    out = {}
    for league_name, urls in LEAGUES.items():
        if not urls.get("table_url"):
            continue
        try:
            r = requests.get(
                urls["table_url"],
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            table_data = []
            tables = soup.find_all("table")
            if tables:
                t = tables[0]
                # Find header row to identify column positions
                header_row = t.find("thead")
                header_cells = []
                if header_row:
                    header_cells = header_row.find_all(["th", "td"])
                
                # Find indices for Played and Points columns by searching header text
                played_idx = None
                points_idx = None
                
                for idx, cell in enumerate(header_cells):
                    text = cell.get_text(strip=True).lower()
                    # Look for "played" or "pl" (but not just "p" as that could be position)
                    if "played" in text or text == "pl":
                        played_idx = idx
                    # Look for "points" or "pts"
                    if "points" in text or text == "pts":
                        points_idx = idx
                
                # If not found in header, try common positions
                # Sky Sports table structure: Pos, Team, Pl, W, D, L, F, A, GD, Pts
                if played_idx is None:
                    played_idx = 2  # Usually 3rd column (0-indexed: 2)
                if points_idx is None:
                    points_idx = -1  # Usually last column
                
                # Get data rows (skip header)
                rows = t.find_all("tr")
                data_rows = rows[1:] if header_row else rows
                
                for row in data_rows[:24]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 3:
                        continue
                    
                    pos = cells[0].get_text(strip=True)
                    # Team name might be in a link or have an image, get all text from cell
                    # Preserve asterisk (*) which indicates point deductions
                    team_cell = cells[1] if len(cells) > 1 else None
                    if team_cell:
                        # Get text, preserving asterisks and other markers
                        team = team_cell.get_text(strip=True)
                        # Remove leading numbers but preserve asterisks
                        team = re.sub(r'^\d+\s*', '', team)
                    else:
                        team = ""
                    
                    # Extract all numeric values from cells (skip position and team)
                    # This helps us find the right columns even if structure varies
                    numeric_values = []
                    signed_numeric_values = []  # For values that can be negative (points, GD)
                    
                    for idx, cell in enumerate(cells[2:], start=2):  # Skip pos (0) and team (1)
                        text = cell.get_text(strip=True)
                        # Handle positive integers
                        if text and text.isdigit():
                            numeric_values.append((idx, int(text)))
                        # Handle signed numbers (+/-) - could be goal difference or points with deductions
                        elif text and (text.startswith('+') or text.startswith('-')):
                            if text[1:].isdigit():
                                signed_numeric_values.append((idx, int(text)))
                    
                    # Prefer header-derived column indices so we don't mix up GD and Pts
                    # (Sky Sports: GD is signed +N/-N, Pts is last column; heuristics were picking GD as Pts)
                    played = ""
                    points = ""

                    if played_idx is not None:
                        idx = played_idx if played_idx >= 0 else len(cells) + played_idx
                        if 0 <= idx < len(cells):
                            candidate = cells[idx].get_text(strip=True)
                            if candidate.isdigit() and 0 <= int(candidate) <= 46:
                                played = candidate

                    if points_idx is not None:
                        idx = points_idx if points_idx >= 0 else len(cells) + points_idx
                        if 0 <= idx < len(cells):
                            candidate = cells[idx].get_text(strip=True)
                            if candidate.isdigit():
                                val = int(candidate)
                                if 0 <= val <= 138:
                                    points = candidate
                            elif candidate.startswith("-") and candidate[1:].isdigit():
                                val = int(candidate)
                                if -50 <= val <= 138:
                                    points = candidate

                    # Heuristic fallback for played: first reasonable number (0-46)
                    if not played:
                        for idx, value in numeric_values:
                            if 0 <= value <= 46:
                                played = str(value)
                                break
                    if not played and len(cells) > 2:
                        candidate = cells[2].get_text(strip=True)
                        if candidate.isdigit() and 0 <= int(candidate) <= 46:
                            played = candidate

                    # Heuristic fallback for points: use last column (Pts), not signed (GD)
                    if not points:
                        candidate = cells[-1].get_text(strip=True)
                        if candidate.isdigit():
                            val = int(candidate)
                            if 0 <= val <= 138:
                                points = candidate
                        elif candidate.startswith("-") and candidate[1:].isdigit():
                            val = int(candidate)
                            if -50 <= val <= 138:
                                points = candidate
                        elif candidate.startswith("+") and candidate[1:].isdigit():
                            pass  # GD, not points
                    if not points:
                        for idx, value in reversed(numeric_values):
                            if 0 <= value <= 138:
                                points = str(value)
                                break
                    if not points:
                        for idx, value in reversed(signed_numeric_values):
                            if -50 <= value <= 138:
                                points = str(value)
                                break
                    
                    table_data.append({"position": pos, "team": team, "played": played, "points": points})
            out[league_name] = {"table": table_data, "last_updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"Error scraping {league_name} table: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            out[league_name] = {"table": [], "last_updated": datetime.now().isoformat(), "error": str(e)}
    return out


# -----------------------------------------------------------------------------
# HTML generation
# -----------------------------------------------------------------------------


def ensure_docs() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)


def render_html(scores: dict, tables: dict) -> None:
    ensure_docs()
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    # Index
    index = env.get_template("index.html")
    sections = []
    for key, label in LEAGUE_NAMES.items():
        links = [{"href": f"{key}_scores.html", "label": f"{label} Scores"}]
        if LEAGUES[key].get("table_url"):
            links.append({"href": f"{key}_table.html", "label": f"{label} Table"})
        sections.append({"name": label, "links": links})
    (DOCS_DIR / "index.html").write_text(
        index.render(sections=sections, last_updated=datetime.now().isoformat()),
        encoding="utf-8",
    )

    # Scores pages
    scores_tpl = env.get_template("scores.html")
    for key, label in LEAGUE_NAMES.items():
        data = scores.get(key, {})
        html = scores_tpl.render(
            competition=key,
            title=f"{label} Scores",
            matches=data.get("matches", []),
            last_updated=data.get("last_updated", ""),
            error=data.get("error"),
        )
        (DOCS_DIR / f"{key}_scores.html").write_text(html, encoding="utf-8")

    # Table pages (leagues only)
    table_tpl = env.get_template("table.html")
    for key, label in LEAGUE_NAMES.items():
        if not LEAGUES[key].get("table_url"):
            continue
        data = tables.get(key, {})
        html = table_tpl.render(
            competition=key,
            title=f"{label} Table",
            rows=data.get("table", []),
            last_updated=data.get("last_updated", ""),
            error=data.get("error"),
        )
        (DOCS_DIR / f"{key}_table.html").write_text(html, encoding="utf-8")


def render_status_page() -> None:
    """Render the status page showing last run times and today's schedule."""
    import json
    
    ensure_docs()
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    
    # Read schedule file
    schedule_file = ROOT / ".github" / "schedule.json"
    schedule_data = {}
    if schedule_file.exists():
        try:
            schedule_data = json.loads(schedule_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    
    # Format times for display
    def format_time(time_tuple_or_list):
        if not time_tuple_or_list:
            return "N/A"
        if isinstance(time_tuple_or_list, list):
            hour, minute = time_tuple_or_list
        else:
            hour, minute = time_tuple_or_list
        return f"{hour:02d}:{minute:02d}"
    
    def format_datetime(iso_string):
        if not iso_string:
            return "Never"
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except (ValueError, AttributeError):
            return iso_string
    
    # Render status page
    status_tpl = env.get_template("status.html")
    html = status_tpl.render(
        scheduler_last_run=format_datetime(schedule_data.get("scheduler_last_run")),
        scraper_last_run=format_datetime(schedule_data.get("scraper_last_run")),
        schedule_date=schedule_data.get("date", "N/A"),
        has_games=schedule_data.get("has_games", False),
        earliest_game=format_time(schedule_data.get("earliest_game")),
        latest_game=format_time(schedule_data.get("latest_game")),
        start_time=format_time(schedule_data.get("start_time")),
        end_time=format_time(schedule_data.get("end_time")),
        final_time=format_time(schedule_data.get("final_time")),
        run_times=schedule_data.get("run_times", []),
        last_updated=datetime.now(timezone.utc).isoformat(),
    )
    (DOCS_DIR / "status.html").write_text(html, encoding="utf-8")


def update_scraper_last_run() -> None:
    """Update the schedule.json file with the scraper's last run time."""
    import json
    
    schedule_file = ROOT / ".github" / "schedule.json"
    schedule_data = {}
    if schedule_file.exists():
        try:
            schedule_data = json.loads(schedule_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    
    schedule_data["scraper_last_run"] = datetime.now(timezone.utc).isoformat()
    schedule_file.write_text(json.dumps(schedule_data, indent=2), encoding="utf-8")


# -----------------------------------------------------------------------------
# Git push
# -----------------------------------------------------------------------------


def git_push(commit_message: str | None = None) -> bool:
    if commit_message is None:
        commit_message = f"Update scores & tables — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    # Add [skip ci] to prevent GitHub Pages build from running
    # This saves GitHub Actions minutes since Pages will auto-deploy anyway
    if "[skip ci]" not in commit_message and "[ci skip]" not in commit_message:
        commit_message = f"{commit_message} [skip ci]"
    
    # Find git executable
    git_exe = shutil.which("git")
    if git_exe is None:
        print("Error: git command not found. Please ensure Git is installed and in your PATH.", file=sys.stderr)
        return False
    
    try:
        subprocess.run([git_exe, "add", "docs"], cwd=ROOT, check=True, capture_output=True)
        commit = subprocess.run(
            [git_exe, "commit", "-m", commit_message],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if commit.returncode != 0:
            out = (commit.stdout or "") + (commit.stderr or "")
            if "nothing to commit" in out.lower():
                return True  # No changes, skip push
            print(f"Git commit error: {out}", file=sys.stderr)
            return False
        subprocess.run([git_exe, "push"], cwd=ROOT, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        err = (e.stderr or e.stdout or b"").decode(errors="replace")
        print(f"Git error: {err}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: git command not found. Please ensure Git is installed and in your PATH.", file=sys.stderr)
        return False


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Scrape scores/tables, update HTML, push to GitHub.")
    parser.add_argument("--no-push", action="store_true", help="Skip git commit & push")
    parser.add_argument("-m", "--message", dest="message", help="Custom git commit message")
    args = parser.parse_args()

    print("Scraping scores...")
    scores = scrape_scores()
    
    # Display scraped scores
    print("\n" + "="*60)
    print("SCRAPED SCORES")
    print("="*60)
    for league_name, data in scores.items():
        matches = data.get("matches", [])
        if matches:
            league_label = LEAGUE_NAMES.get(league_name, league_name)
            print(f"\n{league_label}: {len(matches)} match(es)")
            for match in matches:
                team1 = match.get("team1", "")
                team2 = match.get("team2", "")
                score1 = match.get("score1", "")
                score2 = match.get("score2", "")
                status = match.get("status", "")
                time_str = match.get("date_time", "")
                
                if score1 and score2:
                    print(f"  {team1:30} {score1} - {score2} {team2:30} [{status}]")
                elif time_str:
                    print(f"  {team1:30} vs {team2:30} [{status}] {time_str}")
                else:
                    print(f"  {team1:30} vs {team2:30} [{status}]")
        elif data.get("error"):
            print(f"\n{LEAGUE_NAMES.get(league_name, league_name)}: Error - {data.get('error')}")
    print("="*60 + "\n")
    
    # Check if there are any matches today - exit early if none found to save GitHub Actions minutes
    total_matches = sum(len(data.get("matches", [])) for data in scores.values())
    if total_matches == 0:
        print("No matches found for today. Exiting early to save GitHub Actions minutes.")
        return
    
    print(f"Found {total_matches} matches across all leagues.")
    print("Scraping tables...")
    tables = scrape_tables()
    print("Rendering HTML...")
    render_html(scores, tables)
    
    # Update scraper last run time and render status page
    update_scraper_last_run()
    render_status_page()

    if args.no_push:
        print("Skipping git push (--no-push).")
        return

    print("Pushing to GitHub...")
    if git_push(args.message):
        print("Done.")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
