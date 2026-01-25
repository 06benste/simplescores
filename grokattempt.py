import requests
from bs4 import BeautifulSoup
from datetime import datetime

# Ordinal suffix function
def ordinal(n):
    if 11 <= (n % 100) <= 13:
        return str(n) + 'th'
    else:
        return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

# Get today's date formatted (e.g., "Sunday 25th January")
today = datetime.now()
day_name = today.strftime('%A')
day_ordinal = ordinal(today.day)
month_name = today.strftime('%B')
today_header = f"{day_name} {day_ordinal} {month_name}"

# Fetch the page
url = "https://www.skysports.com/premier-league-scores-fixtures"
response = requests.get(url)
soup = BeautifulSoup(response.text, 'html.parser')

# Find the matching date header
date_header = None
for h3 in soup.find_all('h3'):
    if h3.text.strip() == today_header:
        date_header = h3
        break

today_matches = []
if date_header:
    # Get the <ul> after <hr>
    hr = date_header.find_next('hr')
    if hr:
        ul = hr.find_next('ul')
        if ul:
            for li in ul.find_all('li'):
                a = li.find('a')
                if a:
                    # Teams from img alt
                    imgs = a.find_all('img')
                    home_team = imgs[0]['alt'] if imgs else ''
                    away_team = imgs[1]['alt'] if len(imgs) > 1 else ''
                    
                    # Full text inside <a>
                    match_text = ' '.join(a.stripped_strings)
                    
                    # Status
                    status_span = a.find('span')
                    status = status_span.text.strip() if status_span else 'Upcoming'
                    
                    # Parse scores or time
                    parts = match_text.split()
                    if status == 'Upcoming':
                        # Time is after the last '.'
                        scores_or_time = match_text.split('.')[-1].strip()
                    else:
                        # Scores: account for multi-word teams
                        home_words = home_team.split()
                        len_home = len(home_words)
                        away_words = away_team.split()
                        len_away = len(away_words)
                        home_score = parts[len_home] if len(parts) > len_home and parts[len_home].isdigit() else ''
                        away_score = parts[len_home + 1 + len_away] if len(parts) > len_home + 1 + len_away and parts[len_home + 1 + len_away].isdigit() else ''
                        scores_or_time = f"{home_score} - {away_score}"
                    
                    # Optional channel
                    all_imgs = li.find_all('img')
                    channel = all_imgs[-1]['alt'] if len(all_imgs) > 2 else None
                    
                    today_matches.append({
                        'home_team': home_team,
                        'away_team': away_team,
                        'scores_or_time': scores_or_time,
                        'status': status,
                        'channel': channel,
                        'link': 'https://www.skysports.com' + a['href']
                    })
else:
    print(f"No fixtures found for '{today_header}'. The page may not include this date, or check the format.")

# Output
if today_matches:
    for match in today_matches:
        print(match)
else:
    print("No matches parsed for today.")