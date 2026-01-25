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
        "scores_url": "https://www.skysports.com/premier-league-results",
        "table_url": "https://www.skysports.com/premier-league-table",
    },
    "championship": {
        "scores_url": "https://www.skysports.com/championship-results",
        "table_url": "https://www.skysports.com/championship-table",
    },
    "league_one": {
        "scores_url": "https://www.skysports.com/league-1-results",
        "table_url": "https://www.skysports.com/league-1-table",
    },
    "league_two": {
        "scores_url": "https://www.skysports.com/league-2-results",
        "table_url": "https://www.skysports.com/league-2-table",
    },
    "fa_cup": {
        "scores_url": "https://www.skysports.com/fa-cup-results",
        "table_url": None,
    },
    "carabao_cup": {
        "scores_url": "https://www.skysports.com/carabao-cup-results",
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
    today = date.today()
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


def scrape_scores() -> dict:
    import json
    out = {}
    today = date.today()
    # Format: "Sunday 25th January" - need to add ordinal suffix
    day_name = today.strftime("%A")
    day_num = today.day
    month_name = today.strftime("%B")
    
    # Add ordinal suffix (1st, 2nd, 3rd, 4th, etc.)
    if 10 <= day_num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    
    today_str = f"{day_name} {day_num}{suffix} {month_name}"  # "Sunday 25th January"
    today_str_alt = f"{day_name} {day_num} {month_name}"  # "Sunday 25 January" (fallback)
    
    for league_name, urls in LEAGUES.items():
        try:
            r = requests.get(
                urls["scores_url"],
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            matches = []
            seen = set()
            
            # Find all match sections and check their associated date headers
            match_sections = soup.find_all("div", class_="ui-tournament-matches")
            
            for match_section in match_sections:
                # Find the preceding date header for this match section
                date_header = None
                current = match_section.find_previous()
                
                # Search backwards for the date header
                while current:
                    if current.name == "div" and "ui-sitewide-component-header__wrapper--h3" in current.get("class", []):
                        date_header = current
                        break
                    # Stop if we hit another match section (means we've gone too far)
                    if current.name == "div" and "ui-tournament-matches" in current.get("class", []):
                        break
                    current = current.find_previous()
                
                # If no date header found, skip this section (or include if we want to be lenient)
                section_date = ""
                if date_header:
                    date_span = date_header.find("span", {"data-role": "short-text-target"})
                    if date_span:
                        section_date = date_span.get_text(strip=True)
                
                # Check if this is today's section
                is_today = False
                if section_date:
                    is_today = (
                        section_date.lower() == today_str.lower() or
                        section_date.lower() == today_str_alt.lower() or
                        _extract_date_from_context(section_date.lower(), today)
                    )
                else:
                    # If no date header found, check match data for date
                    # This is a fallback - we'll check individual matches
                    pass
                
                if section_date and not is_today:
                    continue
                
                # Extract matches from this section
                match_items = match_section.find_all("div", class_="ui-sport-match-score")
                
                for match_item in match_items:
                    # Get match data from data-state attribute
                    data_state = match_item.get("data-state")
                    if not data_state:
                        continue
                    
                    try:
                        match_data = json.loads(data_state)
                    except json.JSONDecodeError:
                        continue
                    
                    # Check date in match data as fallback
                    match_date = match_data.get("start", {}).get("date", "")
                    if match_date:
                        match_is_today = (
                            match_date.lower() == today_str.lower() or
                            match_date.lower() == today_str_alt.lower() or
                            _extract_date_from_context(match_date.lower(), today)
                        )
                        # If we have a date in match data and it's not today, skip
                        if not match_is_today:
                            continue
                    elif not is_today:
                        # If no date in match data and section isn't today, skip
                        continue
                    
                    # Extract team names
                    home_team = match_data.get("teams", {}).get("home", {}).get("name", {}).get("full", "")
                    away_team = match_data.get("teams", {}).get("away", {}).get("name", {}).get("full", "")
                    
                    if not home_team or not away_team:
                        continue
                    
                    # Extract scores
                    home_score = match_data.get("teams", {}).get("home", {}).get("score", {}).get("current")
                    away_score = match_data.get("teams", {}).get("away", {}).get("score", {}).get("current")
                    
                    # Determine status
                    match_state = match_data.get("matchState", "").lower()
                    is_fixture = match_data.get("isFixture", False)
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
                    
                    # Extract time
                    time_12hr = match_data.get("start", {}).get("time12hr", "")
                    date_time = time_12hr if time_12hr else ""
                    
                    # Create match entry
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
            
            # If no structured matches found, fall back to old method
            if not matches:
                all_text = soup.get_text(separator=" ", strip=True)
                # Try to find matches using text patterns as fallback
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
                header = t.find("thead") or t.find("tr")
                rows = t.find_all("tr")[1:] if header else t.find_all("tr")
                for row in rows[:24]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) < 3:
                        continue
                    pos = cells[0].get_text(strip=True)
                    team = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    played = points = ""
                    nums = [c.get_text(strip=True) for c in cells if c.get_text(strip=True).isdigit()]
                    if len(nums) >= 2:
                        played, points = nums[-2], nums[-1]
                    table_data.append({"position": pos, "team": team, "played": played, "points": points})
            out[league_name] = {"table": table_data, "last_updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"Error scraping {league_name} table: {e}", file=sys.stderr)
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
