# LIVE Capture Scraper & Dashboard

This repository contains a dynamic scraping tool and local web dashboard to monitor and record live stream concurrent viewer counts across YouTube, Facebook, TikTok, and X (Twitter).

---

## Prerequisites

Before running the application, make sure your machine has the following:

1. **Python 3.8+**
2. **Google Chrome** (Selenium uses Chrome to check stream status and parse viewer counts)

---

## Installation

1. Extract the project ZIP file.
2. Open your terminal and navigate to the project directory:
   ```bash
   cd LIVE_capturing_18Jun
   ```
3. Install the required Python packages:
   ```bash
   pip install requests beautifulsoup4 gspread google-auth google-auth-oauthlib google-auth-httplib2 selenium
   ```

*Note: Modern versions of Selenium (4.x) will automatically download and manage the matching Chrome WebDriver in the background, so you do not need to install `chromedriver` manually.*

---

## Configuration (`config.json`)

The script depends on `config.json` in the root directory for target channels, scheduler intervals, and credentials. 

### 1. Google Sheets Integration (Optional)
If you want to sync the live viewer counts to a Google Spreadsheet:
- **`token_path`**: Set this to the absolute path of your personal Google OAuth 2.0 `token.json` credentials file.
- **`google_sheet_id`**: Set this to the spreadsheet ID of your Google Sheet. Ensure that the Google account linked to your `token.json` has share/edit access to that sheet.

### 2. Local-Only Mode (Offline)
If you do not want to sync with Google Sheets:
- You can leave `"token_path"` blank (`""`) or keep the default value.
- The scraper will output a warning log (`Warning: token.json not found...`) and gracefully fall back to saving all records locally to `live_view_counts.csv` without throwing errors.

---

## How to Run

You can run the capture scraper in two ways:

### A. Web GUI Dashboard (Recommended)
This launches a local web server and lets you configure channels, reorder display sequences, scan profiles using keywords, and manage automated run schedules.

1. Start the server:
   ```bash
   python gui_app.py
   ```
2. Open your web browser and navigate to:
   [http://localhost:8000](http://localhost:8000)

### B. CLI Loop Scheduler
If you want to run the scheduler loop directly in the terminal without opening the web dashboard:

```bash
python live_scraper.py --loop
```

---

## Database Outputs

All scraped data is appended in a flat, platform-based row format to the following targets:
- **Local CSV**: `live_view_counts.csv`
- **Google Sheets**: Appends to the configured sheet ID (if valid `token.json` is set).

**Output Schema**:
`[Time, Channel name, platform, Type, views, title]`

- **Time**: Timestamp in Indochina Time (ICT, UTC+7).
- **Channel name**: Configured name (e.g. `Thai PBS`).
- **platform**: Normalised casing (`YouTube`, `Facebook`, `TikTok`, `X`).
- **Type**: Casing normalised value (`Online` or `TV`).
- **views**: Raw concurrent viewer count (e.g., `8421`).
- **title**: Livestream title and source URL.
