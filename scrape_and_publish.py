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
from datetime import date, datetime
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


def _is_today_match(match: dict, match_text: str = "") -> bool:
    if match.get("status") in ("LIVE", "HT"):
        return True
    text = (match_text or match.get("text") or "").lower()
    today = date.today()
    today_day = today.strftime("%A").lower()
    day_names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    if match.get("status") == "FIXTURE":
        if today_day in text:
            return True
        for d in day_names:
            if d in text and d != today_day:
                return False
        if match.get("date_time") and re.match(r"\d{1,2}:\d{2}[ap]m", match.get("date_time", "")):
            return True
        return True

    if match.get("status") == "FT":
        if today_day in text:
            return True
        for d in day_names:
            if d in text and d != today_day:
                return False
        return True

    return True


def _clean_team(words: list[str], take_last: bool, max_words: int = 3) -> str:
    kept = [w for w in words if w and w[0].isupper() and w.isalpha()]
    if not kept:
        return ""
    slice_ = kept[-max_words:] if take_last else kept[:max_words]
    return " ".join(slice_)[:30]


def scrape_scores() -> dict:
    out = {}
    for league_name, urls in LEAGUES.items():
        try:
            r = requests.get(
                urls["scores_url"],
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser")
            all_text = soup.get_text(separator=" ", strip=True)
            html_text = str(soup)
            matches = []
            seen = set()

            # Live / FT: "Team1 score - score Team2"
            for m in re.finditer(
                r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s+(\d+)\s*[-–—]\s*(\d+)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)",
                all_text,
            ):
                t1_full, s1, s2, t2_full = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
                start = max(0, m.start() - 200)
                ctx = all_text[start : m.start() + 300].lower()
                hp = html_text.lower().find(f"{t1_full} {s1}")
                hctx = html_text[max(0, hp - 300) : hp + 400].lower() if hp >= 0 else ""
                min_pat = r"\d+['\']|['\']\s*$|\d+\s*min"
                live = bool(re.search(min_pat, ctx)) or bool(re.search(min_pat, hctx))
                live_t = "live" in ctx or "live" in hctx
                ht = "half" in ctx or " ht" in ctx or "half time" in hctx
                ft = "full time" in ctx or "finished" in ctx or "final" in ctx or "full time" in hctx
                status = "FT"
                if (live or live_t) and not ft:
                    status = "LIVE"
                elif ht and not ft:
                    status = "HT"
                t1 = _clean_team(t1_full.split(), True)
                t2 = _clean_team(t2_full.split(), False)
                key = f"{t1}_{s1}_{t2}_{s2}"
                if key not in seen and t1 and t2 and len(t1) > 2 and len(t2) > 2:
                    seen.add(key)
                    matches.append({
                        "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                        "status": status, "date_time": "", "text": f"{t1_full} {s1} - {s2} {t2_full}",
                    })

            # Completed: "Team1, score. Team2, score. Full time."
            for m in re.findall(
                r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*),\s+(\d+)\.\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*),\s+(\d+)\.\s+([^.]+)",
                all_text,
            )[:50]:
                t1_full, s1, t2_full, s2, st = m[0].strip(), m[1], m[2].strip(), m[3], m[4].strip().lower()
                key = f"{t1_full}_{s1}_{t2_full}_{s2}"
                if key in seen:
                    continue
                t1 = _clean_team(t1_full.split(), True)
                t2 = _clean_team(t2_full.split(), False)
                status = "FT"
                if "live" in st or "in progress" in st:
                    status = "LIVE"
                elif "half time" in st or " ht" in st:
                    status = "HT"
                elif "extra time" in st or " aet" in st:
                    status = "AET"
                if t1 and t2 and len(t1) > 2 and len(t2) > 2:
                    seen.add(key)
                    matches.append({
                        "team1": t1, "team2": t2, "score1": s1, "score2": s2,
                        "status": status, "date_time": "",
                        "text": f"{t1_full}, {s1}. {t2_full}, {s2}. {m[4]}",
                    })

            # Fixtures: "Team1 vs Team2. Kick-off at 12:30pm"
            for m in re.findall(
                r"([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\s+vs\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)\.\s+Kick-off\s+at\s+(\d{1,2}:\d{2}[ap]m)",
                all_text,
                re.I,
            )[:30]:
                t1_full, t2_full, dt = m[0].strip(), m[1].strip(), m[2].strip()
                t1 = _clean_team(t1_full.split(), True)
                t2 = _clean_team(t2_full.split(), False)
                key = f"{t1}_v_{t2}_{dt}"
                if key not in seen and t1 and t2:
                    seen.add(key)
                    matches.append({
                        "team1": t1, "team2": t2, "score1": "", "score2": "",
                        "status": "FIXTURE", "date_time": dt,
                        "text": f"{t1_full} vs {t2_full}. Kick-off at {dt}",
                    })

            if not matches:
                for tag in soup.find_all(["div", "span", "p"], string=lambda x: x and (" - " in str(x) or "v " in str(x).lower()))[:20]:
                    t = tag.get_text(strip=True)
                    if 5 < len(t) < 150:
                        st = "FT"
                        if "LIVE" in t.upper() or "HT" in t.upper():
                            st = "LIVE"
                        elif any(x in t.upper() for x in ["KICK", "PM", "AM"]):
                            st = "FIXTURE"
                        matches.append({"text": t, "status": st})

            today_matches = [m for m in matches if _is_today_match(m, m.get("text", ""))]
            out[league_name] = {"matches": today_matches, "last_updated": datetime.now().isoformat()}
        except Exception as e:
            print(f"Error scraping {league_name} scores: {e}", file=sys.stderr)
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


# -----------------------------------------------------------------------------
# Git push
# -----------------------------------------------------------------------------


def git_push(commit_message: str | None = None) -> bool:
    if commit_message is None:
        commit_message = f"Update scores & tables — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
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
    print("Scraping tables...")
    tables = scrape_tables()
    print("Rendering HTML...")
    render_html(scores, tables)

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
