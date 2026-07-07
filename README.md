# LIVE Capture Scraper & Dashboard

This repository contains a dynamic scraping tool and local web dashboard to monitor and record live stream concurrent viewer counts across YouTube, Facebook, TikTok, and X (Twitter).

---

## Prerequisites

Before running the application, make sure your machine has the following:

1. **Python 3.8+**
2. **Google Chrome** (Selenium uses Chrome to check stream status and parse viewer counts)

---

## Installation

1. Clone or extract the project files.
2. Open your terminal and navigate to the project directory:
   ```bash
   cd LIVE-Scraper
   ```
3. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

*Note: Modern versions of Selenium (4.x) automatically manage and download the matching Chrome WebDriver in the background, so you do not need to download `chromedriver` manually.*

---

## Usage Guidlines

Some of the available features of this program requires logging in its respective platform via the persistent chrome profile.

### 1. Facebook LIVE comment scraping

### 2. TikTok LIVE views and comment scraping

*Note: Scraping Facebook LIVE's views alone is possible without a logged in session, but comment scraping requires one.*

---

## Configuration (`config.json`)

The script depends on `config.json` in the root directory for target channels, scheduler intervals, and credentials. 

### 1. Google Sheets Integration (Optional)
If you want to sync the live viewer counts to a Google Spreadsheet:
- **`token_path`**: Set this to the absolute path of your Google OAuth 2.0 `token.json` credentials file.
- **`google_sheet_id`**: Set this to the spreadsheet ID of your Google Sheet. Ensure that the Google account linked to your `token.json` has share/edit access to that sheet.

### 2. Local-Only Mode (Offline)
If you do not want to sync with Google Sheets:
- You can leave `"token_path"` blank (`""`) or delete the key.
- The scraper will output a warning log (`Warning: token.json not found...`) and gracefully fall back to saving all records locally to `live_view_counts.csv` without throwing errors.

---

## How to Run

### A. Web GUI Dashboard (Recommended)
This launches a local web server and lets you configure channels, reorder display sequences, scan profiles using keywords, and manage automated run schedules.

1. Start the server:
   ```bash
   python gui_app.py
   ```
2. Open your web browser and navigate to:
   [http://localhost:8000](http://localhost:8000)

#### Headless / Remote Environments
If you are running the dashboard on a headless server or CI runner (where you don't want Chrome to open locally, and you don't want the server to shut down automatically when the browser closes), set the following environment variables:
* `DISABLE_BROWSER=1`: Stops the server from attempting to auto-open Chrome in App Mode.
* `DISABLE_HEARTBEAT=1`: Disables the heartbeat auto-shutdown watcher so the server stays alive indefinitely.

For example, on Linux/macOS:
```bash
DISABLE_BROWSER=1 DISABLE_HEARTBEAT=1 python gui_app.py
```
Or on Windows PowerShell:
```powershell
$env:DISABLE_BROWSER="1"
$env:DISABLE_HEARTBEAT="1"
python gui_app.py
```

### B. CLI Loop Scheduler
If you want to run the scheduler loop directly in the terminal without opening the web dashboard:

```bash
python live_scraper.py --loop
```

---

## Compiling Standalone Executables

You can compile this application into a standalone binary using **PyInstaller**. When compiled, `live_scraper.py` and all Python dependencies are packed inside the binary. You only need to bundle the HTML/CSS/JS frontend files.

First, install PyInstaller:
```bash
pip install pyinstaller
```

### 1. Compile on Windows (produces `.exe`)
Run this in Command Prompt or PowerShell. Note the semicolon (`;`) path separator:
```powershell
pyinstaller --onefile --noconsole --clean `
  --collect-submodules=selenium `
  --add-data "index.html;." `
  --add-data "index.css;." `
  --add-data "index.js;." `
  gui_app.py
```
*The compiled binary will be located in `dist/gui_app.exe`.*

### 2. Compile on macOS (produces `.app` bundle)
Run this in Terminal. Note the colon (`:`) path separator:
```bash
pyinstaller --onedir --noconsole --clean \
  --collect-submodules=selenium \
  --add-data "index.html:." \
  --add-data "index.css:." \
  --add-data "index.js:." \
  gui_app.py
```
*The compiled application will be located in `dist/gui_app.app`.*

---

## Remote Testing on GitHub Actions

A manual workflow is configured in `.github/workflows/remote-test.yml` to test the application on a remote Windows environment using **Bore** (a public TCP tunnel).

### How to use:
1. Go to your repository **Actions** tab on GitHub.
2. Select **Remote Debug and Test** on the left menu.
3. Click **Run workflow** and select a mode:
   * **`web-tunnel`**: Exposes the Web GUI. Copy the `bore.pub:xxxxx` link printed in the logs and open it in your Mac's browser.
   * **`rdp`**: Configures Windows Remote Desktop. Connect to the printed `bore.pub:xxxxx` using Microsoft Remote Desktop with username `runneradmin` and password `LiveScraper123!`.

---

## Database Outputs

All scraped data is appended in a flat, platform-based row format to the following targets:
- **Local CSV**: `live_view_counts.csv`
- **Google Sheets**: Appends to the configured sheet ID (if valid `token.json` is set).

**Output Schema**:
`[Time, Channel name, platform, Type, views, title]`

- **Time**: Timestamp in Indochina Time (ICT, UTC+7).
- **Channel name**: Configured name (e.g., `Thai PBS`).
- **platform**: Normalised casing (`YouTube`, `Facebook`, `TikTok`, `X`).
- **Type**: Casing normalised value (`Online` or `TV`).
- **views**: Raw concurrent viewer count (e.g., `8421`).
- **title**: Livestream title and source URL.
