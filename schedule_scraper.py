#!/usr/bin/env python3
"""
Fetch today's fixtures and update the scraper workflow cron schedule.
Runs daily at 2am to set up the day's scraping schedule.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
WORKFLOW_FILE = ROOT / ".github" / "workflows" / "update-scores.yml"

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


def calculate_cron_schedule(fixture_times: list[tuple[int, int]]) -> list[str]:
    """Calculate cron schedule based on fixture times.
    
    Schedule:
    - Start 30 min before earliest game
    - Run every 30 minutes during games
    - End 115 minutes after last game starts
    - Final update 2.5 hours (150 minutes) after last game starts
    
    Note: Times are assumed to be in UK time. We need to convert to UTC.
    UK is UTC+0 (GMT) or UTC+1 (BST). For simplicity, assuming UTC+0.
    """
    if not fixture_times:
        return []
    
    # Find earliest and latest game times (in UK time)
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
    
    # Generate cron entries for every 30 minutes
    # Note: GitHub Actions uses UTC, so we use times as-is (assuming UK = UTC for now)
    cron_entries = []
    current = start_time
    
    # Round start time to nearest 30-minute mark
    if current.minute % 30 != 0:
        current = current.replace(minute=(current.minute // 30) * 30)
    
    while current <= end_time:
        cron_entries.append(f"    - cron: '{current.minute} {current.hour} * * *'")
        current += timedelta(minutes=30)
    
    # Add final update if it's more than 30 minutes after the last scheduled run
    if final_time > end_time:
        cron_entries.append(f"    - cron: '{final_time.minute} {final_time.hour} * * *'")
    
    return cron_entries


def update_workflow_schedule(cron_entries: list[str]) -> bool:
    """Update the workflow file with new cron schedule."""
    if not WORKFLOW_FILE.exists():
        print(f"Workflow file not found: {WORKFLOW_FILE}", file=__import__("sys").stderr)
        return False
    
    content = WORKFLOW_FILE.read_text(encoding="utf-8")
    
    # Find the schedule section and replace it
    # Pattern: match from "schedule:" through all cron entries until "workflow_dispatch:"
    pattern = r"(on:\s+schedule:\s+)(.*?)(\s+workflow_dispatch:)"
    
    if cron_entries:
        # Add comment and cron entries
        new_schedule = "    # Auto-updated by schedule-updater workflow\n"
        new_schedule += "    # Schedule based on today's fixtures\n"
        new_schedule += "\n".join(cron_entries) + "\n"
    else:
        # No games today - use a minimal schedule that won't run
        new_schedule = "    # Auto-updated by schedule-updater workflow\n"
        new_schedule += "    # No games scheduled today\n"
        new_schedule += "    - cron: '0 2 * * *'  # Placeholder - won't trigger scraper\n"
    
    new_content = re.sub(pattern, r"\1" + new_schedule + r"\3", content, flags=re.DOTALL)
    
    if new_content == content:
        print("No changes needed to workflow file.")
        return False
    
    WORKFLOW_FILE.write_text(new_content, encoding="utf-8")
    print(f"Updated workflow schedule with {len(cron_entries)} cron entries.")
    return True


def main() -> None:
    print("Fetching today's fixtures...")
    fixture_times = fetch_today_fixtures()
    
    if not fixture_times:
        print("No fixtures found for today.")
        # Update workflow to not run (or run once at 2am as placeholder)
        cron_entries = []
    else:
        print(f"Found {len(fixture_times)} fixtures for today.")
        print(f"Earliest: {min(fixture_times)}, Latest: {max(fixture_times)}")
        cron_entries = calculate_cron_schedule(fixture_times)
        print(f"Generated {len(cron_entries)} cron schedule entries.")
    
    if update_workflow_schedule(cron_entries):
        print("Workflow schedule updated successfully.")
        print("Note: Commit and push this change for it to take effect.")
    else:
        print("No workflow update needed.")


if __name__ == "__main__":
    main()
