#!/usr/bin/env python3
"""
Fetch today's fixtures and update the scraper workflow cron schedule.
Runs daily at 2am to set up the day's scraping schedule.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
SCHEDULE_FILE = ROOT / ".github" / "schedule.json"
TEMPLATES_DIR = ROOT / "templates"
DOCS_DIR = ROOT / "docs"

LEAGUES = {
    "premier_league": {
        "scores_url": "https://www.skysports.com/premier-league-results",
    },
    "championship": {
        "scores_url": "https://www.skysports.com/championship-results",
    },
    "league_one": {
        "scores_url": "https://www.skysports.com/league-1-results",
    },
    "league_two": {
        "scores_url": "https://www.skysports.com/league-2-results",
    },
    "fa_cup": {
        "scores_url": "https://www.skysports.com/fa-cup-results",
    },
    "carabao_cup": {
        "scores_url": "https://www.skysports.com/carabao-cup-results",
    },
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def parse_time_12hr(time_str: str) -> tuple[int, int] | None:
    """Parse time like '2.00pm' or '2:00pm' to (hour, minute) in 24-hour format."""
    if not time_str:
        return None
    
    # Remove dots and spaces
    time_str = time_str.replace(".", "").replace(" ", "").lower()
    
    # Match patterns like "2:00pm", "200pm", "14:00", etc.
    match = re.match(r"(\d{1,2}):?(\d{2})(am|pm)?", time_str)
    if not match:
        return None
    
    hour = int(match.group(1))
    minute = int(match.group(2))
    period = match.group(3) or ""
    
    # Convert to 24-hour
    if period == "pm" and hour != 12:
        hour += 12
    elif period == "am" and hour == 12:
        hour = 0
    
    return (hour, minute)


def fetch_today_fixtures() -> list[tuple[int, int]]:
    """Fetch all fixture times for today across all leagues. Returns list of (hour, minute) tuples."""
    today = date.today()
    day_name = today.strftime("%A")
    day_num = today.day
    month_name = today.strftime("%B")
    
    # Add ordinal suffix
    if 10 <= day_num % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day_num % 10, "th")
    
    today_str = f"{day_name} {day_num}{suffix} {month_name}"
    today_str_alt = f"{day_name} {day_num} {month_name}"
    
    fixture_times = []
    
    for league_name, urls in LEAGUES.items():
        try:
            r = requests.get(
                urls["scores_url"],
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            
            # Find all match sections
            match_sections = soup.find_all("div", class_="ui-tournament-matches")
            
            for match_section in match_sections:
                # Find preceding date header
                date_header = None
                current = match_section.find_previous()
                
                while current:
                    if current.name == "div" and "ui-sitewide-component-header__wrapper--h3" in current.get("class", []):
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
                
                # Check if this is today's section
                is_today = (
                    section_date.lower() == today_str.lower() or
                    section_date.lower() == today_str_alt.lower()
                )
                
                if not is_today:
                    continue
                
                # Extract match times
                match_items = match_section.find_all("div", class_="ui-sport-match-score")
                
                for match_item in match_items:
                    data_state = match_item.get("data-state")
                    if not data_state:
                        continue
                    
                    try:
                        match_data = json.loads(data_state)
                    except json.JSONDecodeError:
                        continue
                    
                    # Check date in match data
                    match_date = match_data.get("start", {}).get("date", "")
                    if match_date and match_date.lower() not in (today_str.lower(), today_str_alt.lower()):
                        continue
                    
                    # Extract time
                    time_12hr = match_data.get("start", {}).get("time12hr", "")
                    time_24hr = match_data.get("start", {}).get("time", "")
                    
                    # Prefer 24-hour format, fall back to 12-hour
                    if time_24hr:
                        try:
                            hour, minute = map(int, time_24hr.split(":"))
                            fixture_times.append((hour, minute))
                        except (ValueError, AttributeError):
                            pass
                    elif time_12hr:
                        parsed = parse_time_12hr(time_12hr)
                        if parsed:
                            fixture_times.append(parsed)
        
        except Exception as e:
            print(f"Error fetching fixtures for {league_name}: {e}", file=__import__("sys").stderr)
            continue
    
    return fixture_times


def calculate_schedule_times(fixture_times: list[tuple[int, int]]) -> dict:
    """Calculate schedule times based on fixture times.
    
    Returns a dict with:
    - start_time: (hour, minute) - 30 min before earliest game
    - end_time: (hour, minute) - 115 min after last game
    - final_time: (hour, minute) - 2.5h after last game
    - run_times: list of (hour, minute) tuples for every 10 minutes
    
    Note: Times are in UTC (assuming UK = UTC for simplicity)
    """
    if not fixture_times:
        return {
            "has_games": False,
            "run_times": []
        }
    
    # Find earliest and latest game times (in UK time, treated as UTC)
    earliest = min(fixture_times)
    latest = max(fixture_times)
    
    # Start 30 minutes before earliest game
    start_hour, start_min = earliest
    start_time = datetime.combine(date.today(), datetime.min.time().replace(hour=start_hour, minute=start_min))
    start_time -= timedelta(minutes=30)
    
    # End 115 minutes after last game starts
    end_hour, end_min = latest
    end_time = datetime.combine(date.today(), datetime.min.time().replace(hour=end_hour, minute=end_min))
    end_time += timedelta(minutes=115)
    
    # Final update 2.5 hours (150 minutes) after last game
    final_time = datetime.combine(date.today(), datetime.min.time().replace(hour=end_hour, minute=end_min))
    final_time += timedelta(minutes=150)
    
    # Generate run times for every 10 minutes
    run_times = []
    current = start_time
    
    # Round start time to nearest 10-minute mark
    if current.minute % 10 != 0:
        current = current.replace(minute=(current.minute // 10) * 10)
    
    while current <= end_time:
        run_times.append((current.hour, current.minute))
        current += timedelta(minutes=10)
    
    # Add final update if it's more than 10 minutes after the last scheduled run
    if final_time > end_time:
        run_times.append((final_time.hour, final_time.minute))
    
    return {
        "has_games": True,
        "date": date.today().isoformat(),
        "earliest_game": earliest,
        "latest_game": latest,
        "start_time": (start_time.hour, start_time.minute),
        "end_time": (end_time.hour, end_time.minute),
        "final_time": (final_time.hour, final_time.minute),
        "run_times": run_times
    }


def update_schedule_file(schedule_data: dict) -> bool:
    """Update the schedule JSON file with today's schedule."""
    # Ensure .github directory exists
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Read existing schedule if it exists
    existing_schedule = {}
    if SCHEDULE_FILE.exists():
        try:
            existing_schedule = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    
    # Add last run time for scheduler (use UTC)
    schedule_data["scheduler_last_run"] = datetime.now(timezone.utc).isoformat()
    
    # Check if schedule has changed
    if existing_schedule.get("date") == schedule_data.get("date") and \
       existing_schedule.get("run_times") == schedule_data.get("run_times"):
        # Still update last_run_time even if schedule unchanged
        SCHEDULE_FILE.write_text(json.dumps(schedule_data, indent=2), encoding="utf-8")
        print("Schedule unchanged, but updated last run time.")
        return False
    
    # Write new schedule
    SCHEDULE_FILE.write_text(json.dumps(schedule_data, indent=2), encoding="utf-8")
    print(f"Updated schedule file with {len(schedule_data.get('run_times', []))} run times.")
    return True


def render_status_page(schedule_data: dict) -> None:
    """Render the status page showing last run times and today's schedule."""
    from jinja2 import Environment, FileSystemLoader
    
    try:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        
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
        print("Status page rendered successfully.")
    except Exception as e:
        print(f"Error rendering status page: {e}", file=__import__("sys").stderr)
        import traceback
        traceback.print_exc()


def main() -> None:
    print("Fetching today's fixtures...")
    fixture_times = fetch_today_fixtures()
    
    if not fixture_times:
        print("No fixtures found for today.")
        schedule_data = {"has_games": False, "date": date.today().isoformat(), "run_times": []}
    else:
        print(f"Found {len(fixture_times)} fixtures for today.")
        print(f"Earliest: {min(fixture_times)}, Latest: {max(fixture_times)}")
        schedule_data = calculate_schedule_times(fixture_times)
        print(f"Generated schedule with {len(schedule_data['run_times'])} run times.")
    
    # Update schedule file (this adds scheduler_last_run to schedule_data)
    schedule_changed = update_schedule_file(schedule_data)
    if schedule_changed:
        print("Schedule file updated successfully.")
    else:
        print("No schedule update needed.")
    
    # Re-read schedule file to get the latest data including last run times
    if SCHEDULE_FILE.exists():
        try:
            schedule_data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            pass
    
    # Render status page so it's updated when schedule changes
    render_status_page(schedule_data)


if __name__ == "__main__":
    main()
