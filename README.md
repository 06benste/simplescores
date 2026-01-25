# Simple Scores 2

Python script that **obtains scores and league tables** from Sky Sports, **updates static HTML files**, and **pushes them to a GitHub static site** (GitHub Pages). Configured for **https://github.com/06benste/simplescores**.

## Setup

1. **Clone the repo** (or use this folder as the repo):

   ```bash
   git clone https://github.com/06benste/simplescores.git
   cd simplescores
   ```

2. **Create a virtualenv and install deps** (from project root):

   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate   # macOS/Linux
   pip install -r requirements.txt
   ```

3. **Initialize Git** (if not already) and set remote:

   ```bash
   git init
   git remote add origin https://github.com/06benste/simplescores.git
   ```
   If `origin` already exists, use `git remote set-url origin https://github.com/06benste/simplescores.git` instead.

4. **Enable GitHub Pages** for the repo:
   - **Settings → Pages → Source**: “Deploy from a branch”
   - **Branch**: `main` (or your default), **folder**: `/docs`
   - Save. The site will be at **https://06benste.github.io/simplescores/**.

5. **Commit and push**: Add the project (including `docs/` and `.github/workflows/`), then push:

   ```bash
   git add .
   git commit -m "Initial commit"
   git push -u origin main
   ```

   The script writes all HTML under `docs/`. The first run will generate `index.html` and the competition pages there.

## Usage

From the project root:

```bash
python scrape_and_publish.py
```

This will:

1. Scrape scores (today’s matches) and tables from Sky Sports.
2. Render `templates/` into `docs/` (e.g. `index.html`, `premier_league_scores.html`, `premier_league_table.html`, etc.).
3. `git add docs`, commit with a timestamped message, and `git push`.

### Options

- **`--no-push`**: Scrape and update HTML only; skip `git commit` and `git push`.
- **`-m "Your message"`** / **`--message "Your message"`**: Use a custom commit message.

### Examples

```bash
python scrape_and_publish.py --no-push
python scrape_and_publish.py -m "Update Saturday scores"
```

## Automatic updates

Run the script on a schedule:

- **Windows**: Task Scheduler.
- **macOS/Linux**: `cron`, e.g. every 15 minutes:

  ```cron
  */15 * * * * cd /path/to/simplescores2 && .venv/bin/python scrape_and_publish.py
  ```

- **GitHub Actions**: A workflow at `.github/workflows/update-scores.yml` runs on a schedule (weekends 11-23h UTC, weekdays 12-23h UTC, every 15 min) and on manual **Run workflow**. It scrapes, updates `docs/`, and pushes. Ensure **Settings -> Actions -> General -> Workflow permissions** is "Read and write".

## What gets generated

- **`docs/index.html`**: Index with links to each competition’s scores and (where applicable) table.
- **`docs/<league>_scores.html`**: Scores for Premier League, Championship, League One, League Two, FA Cup, Carabao Cup.
- **`docs/<league>_table.html`**: League tables (Premier League, Championship, League One, League Two only; cups have no table).

## Requirements

- Python 3.9+
- Git installed and configured (for push).
- Network access to Sky Sports and your GitHub remote.
