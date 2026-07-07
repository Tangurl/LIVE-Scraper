import os
import sys
import io
if sys.platform == "win32":
    if sys.stdout is None:  # happens with --noconsole
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    elif getattr(sys.stdout, "encoding", "").lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    elif getattr(sys.stderr, "encoding", "").lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
import signal
import re
import csv
import json
import time
import argparse
import requests
import bs4
import queue
import threading
import concurrent.futures
import gspread
from datetime import datetime, timezone, timedelta
from google.oauth2.credentials import Credentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
import selenium.webdriver.chrome.webdriver
import selenium.webdriver.chrome.options
import selenium.webdriver.chrome.service
import atexit

# Timezone configurations (ICT = Indochina Time = UTC+7)
ICT_TZ = timezone(timedelta(hours=7))

# Global registry of active Selenium drivers to ensure cleanup on exit
active_drivers = []
active_drivers_lock = threading.Lock()

def register_driver(driver):
    with active_drivers_lock:
        active_drivers.append(driver)
    
    # Wrap driver.quit to unregister itself on call
    original_quit = driver.quit
    def wrapped_quit():
        try:
            original_quit()
        finally:
            unregister_driver(driver)
    driver.quit = wrapped_quit

def unregister_driver(driver):
    with active_drivers_lock:
        if driver in active_drivers:
            active_drivers.remove(driver)

def cleanup_active_drivers():
    with active_drivers_lock:
        drivers_to_quit = list(active_drivers)
    
    if drivers_to_quit:
        print(f"[Cleanup] Quitting {len(drivers_to_quit)} active Selenium driver(s) on exit...")
        for driver in drivers_to_quit:
            try:
                driver.quit()
            except Exception as e:
                pass

# Register the exit handler
atexit.register(cleanup_active_drivers)

# Path resolution for PyInstaller
def get_external_dir():
    if getattr(sys, 'frozen', False):
        path = os.path.abspath(sys.executable)
        # If running inside a macOS .app bundle, go up 4 levels to the folder containing the bundle
        if "Contents/MacOS" in path:
            for _ in range(4):
                path = os.path.dirname(path)
            return path
        return os.path.dirname(path)
    else:
        return os.path.dirname(os.path.abspath(__file__))

EXTERNAL_DIR = get_external_dir()
CONFIG_PATH = os.path.join(EXTERNAL_DIR, "config.json")
CHANNELS = {}
SPREADSHEET_ID = ""
TOKEN_PATH = ""
SCRAPE_COMMENTS = True
COMMENT_DURATION = 30
MAX_CHECKING_WORKERS = 6
MAX_COMMENT_WORKERS = 5

try:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            CHANNELS = config_data.get("channels", {})
            SPREADSHEET_ID = config_data.get("google_sheet_id", "")
            token_val = config_data.get("token_path", "")
            if token_val and not os.path.isabs(token_val):
                TOKEN_PATH = os.path.join(EXTERNAL_DIR, token_val)
            else:
                TOKEN_PATH = token_val
            SCRAPE_COMMENTS = config_data.get("scrape_comments", True)
            COMMENT_DURATION = config_data.get("comment_scrape_duration_seconds", 30)
            MAX_CHECKING_WORKERS = config_data.get("max_checking_workers", 6)
            MAX_COMMENT_WORKERS = config_data.get("max_comment_workers", 5)
    else:
        print(f"Warning: config.json not found at {CONFIG_PATH}. Using empty configurations.")
except Exception as e:
    print(f"Error loading config.json: {e}")

CSV_FILE = "live_view_counts.csv"

# Google Sheets configurations
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def write_to_google_sheet(rows):
    """Appends scraped rows to the designated Google Spreadsheet."""
    if not os.path.exists(TOKEN_PATH):
        print(f"Warning: token.json not found at {TOKEN_PATH}. Skipping Google Sheets update.")
        return
        
    try:
        print(f"Connecting to Google Sheets (Spreadsheet ID: {SPREADSHEET_ID})...")
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        
        # Format rows for gspread append (list of lists)
        # Columns: Time, Channel name, platform, Type, views, title
        values_to_append = []
        for r in rows:
            values_to_append.append([
                r["Time"],
                r["Channel name"],
                r["platform"],
                r["Type"],
                r["views"],
                r["title"]
            ])
            
        print(f"Appending {len(values_to_append)} rows to Google Sheet...")
        sheet.append_rows(values_to_append)
        print("Google Sheet successfully updated!")
    except Exception as e:
        print(f"Error updating Google Sheet: {e}")

def load_recent_saved_comments(csv_path, limit=200):
    """Loads the last N comments from the CSV file for deduplication."""
    if not os.path.exists(csv_path):
        return set()
    recent = set()
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            # Skip header
            header = next(reader, None)
            # Read all rows into a list
            rows = list(reader)
            # Get the last limit rows
            for r in rows[-limit:]:
                if len(r) >= 7:
                    # 7-column schema: Timestamp, Channel, Platform, Live URL, Title, Username, Comment Text
                    recent.add((r[2].lower(), r[3].strip(), r[5].strip(), r[6].strip()))
                elif len(r) == 6:
                    # Old 6-column schema: Timestamp, Channel, Platform, Live URL, Username, Comment Text
                    recent.add((r[2].lower(), r[3].strip(), r[4].strip(), r[5].strip()))
    except Exception as e:
        print(f"  [Comment Scraper] Error reading recent comments for deduplication: {e}")
    return recent

def get_element_text_with_emojis(driver, element):
    """Extracts text from an element, replacing emoji <img> tags with their alt attributes."""
    js_script = """
    function getMessageText(element) {
        var text = "";
        var childNodes = element.childNodes;
        for (var i = 0; i < childNodes.length; i++) {
            var node = childNodes[i];
            if (node.nodeType === Node.TEXT_NODE) {
                text += node.textContent;
            } else if (node.nodeType === Node.ELEMENT_NODE) {
                if (node.tagName === 'IMG') {
                    var alt = node.getAttribute('alt');
                    if (alt) {
                        text += alt;
                    }
                } else {
                    text += getMessageText(node);
                }
            }
        }
        return text;
    }
    return getMessageText(arguments[0]);
    """
    try:
        return driver.execute_script(js_script, element).strip()
    except Exception as e:
        return element.text.strip()

def scrape_youtube_comments_live(driver):
    """Parses live chat comments from YouTube live player iframe."""
    comments = []
    try:
        # Switch to chat frame
        try:
            iframe = driver.find_element(By.ID, "chatframe")
            driver.switch_to.frame(iframe)
            time.sleep(1)
        except:
            # Fallback if no iframe found
            pass
            
        elements = driver.find_elements(By.CSS_SELECTOR, "yt-live-chat-text-message-renderer")
        for el in elements:
            try:
                author_el = el.find_element(By.CSS_SELECTOR, "#author-name")
                author = get_element_text_with_emojis(driver, author_el)
                message_el = el.find_element(By.CSS_SELECTOR, "#message")
                message = get_element_text_with_emojis(driver, message_el)
                if author or message:
                    comments.append((author, message))
            except:
                pass
                
        # Always switch back to default content
        driver.switch_to.default_content()
    except Exception as e:
        print(f"  [Comment Scraper] YouTube parse error: {e}")
    return comments

def is_old_facebook_timestamp(text):
    text_clean = text.lower().strip()
    # Parse number and unit (allowing optional trailing dot for abbreviation units like ชม.)
    match = re.match(r'^(\d+)\s*(m|h|d|y|w|min|hr|day|yr|wk|นาที|ชม|ชั่วโมง|วัน|ปี|สัปดาห์)\.?s?$', text_clean)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        # Minute units: allow 1m/1 min/1 นาที and 2m/2 min/2 นาที to pass as fresh comments
        if unit in ["m", "min", "นาที"]:
            if num > 2:
                return True
        else:
            # Other units (hour, day, week, year) are always old
            return True
        
    # Matches date patterns (e.g. '25 jun', 'jun 25', '25 june 2026')
    months_pattern = r'(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|ม\.ค\.|ก\.พ\.|มี\.ค\.|เม\.ย\.|พ\.ค\.|มิ\.ย\.|ก\.ค\.|ส\.ค\.|ก\.ย\.|ต\.ค\.|พ\.ย\.|ธ\.ค\.)'
    if re.search(months_pattern, text_clean):
        return True
        
    # Matches numeric date patterns (e.g. '12/10/26', '2026-10-12')
    if re.search(r'\d+[\/\-]\d+', text_clean):
        return True
        
    return False

def scrape_facebook_comments_live(driver):
    """Parses live comments from Facebook video page using role='article' blocks."""
    comments = []

    try:
        articles = driver.find_elements(By.XPATH, "//*[@role='article']")
        for art in articles:
            try:
                # Find author name
                author_el = None
                author_els = art.find_elements(By.CSS_SELECTOR, "a[role='link'][aria-hidden='false']")
                if author_els:
                    author_el = author_els[0]
                else:
                    author_els = art.find_elements(By.XPATH, ".//a[@role='link']//span[@dir='auto']")
                    if author_els:
                        author_el = author_els[0]
                
                if not author_el:
                    continue
                
                author = get_element_text_with_emojis(driver, author_el).strip()
                if not author:
                    continue
                
                # Find message text
                message_el = None
                message_els = art.find_elements(By.CSS_SELECTOR, "span[dir='auto'][lang]")
                if message_els:
                    message_el = message_els[0]
                else:
                    message_els = art.find_elements(By.XPATH, ".//div[@dir='auto'][contains(@style, 'text-align: start')]")
                    if message_els:
                        message_el = message_els[0]
                
                if not message_el:
                    continue
                
                message = get_element_text_with_emojis(driver, message_el).strip()
                if not message:
                    continue
                
                # Filter out control buttons
                if author.lower() in ["like", "reply", "share", "สด", "live"] or message.lower() in ["like", "reply", "share"]:
                    continue
                
                # Find timestamp in li elements
                timestamp = ""
                li_elements = art.find_elements(By.TAG_NAME, "li")
                for li in li_elements:
                    text = li.text.strip()
                    if text:
                        timestamp = text
                        break
                
                if timestamp:
                    if is_old_facebook_timestamp(timestamp):
                        continue
                        
                comments.append((author, message))
            except:
                pass
    except Exception as e:
        print(f"  [Comment Scraper] Facebook parse error: {e}")
    return comments

def scrape_tiktok_comments_live(driver):
    """Parses live chat comments from TikTok Live using dkahau container class."""
    comments = []
    try:
        rows = driver.find_elements(By.XPATH, "//*[contains(@class, 'css-dkahau')]")
        for row in rows:
            try:
                # Username is in div with class containing 'text-UIText3'
                try:
                    user_el = row.find_element(By.CSS_SELECTOR, "div[class*='text-UIText3']")
                    username = get_element_text_with_emojis(driver, user_el)
                except:
                    username = ""
                
                # Comment text is in the last child element of the row container
                children = row.find_elements(By.XPATH, "./*")
                if children and username:
                    message = get_element_text_with_emojis(driver, children[-1])
                    if message and message != username:
                        comments.append((username, message))
            except:
                pass
    except Exception as e:
        print(f"  [Comment Scraper] TikTok parse error: {e}")
    return comments

def scrape_x_comments_live(driver):
    """Parses live comments / tweet replies from X status/broadcast pages."""
    comments = []
    try:
        tweet_elements = driver.find_elements(By.CSS_SELECTOR, "[data-testid='tweet']")
        for tweet in tweet_elements:
            try:
                user_el = tweet.find_element(By.CSS_SELECTOR, "[data-testid='User-Name']")
                user_text_full = get_element_text_with_emojis(driver, user_el)
                user_text = user_text_full.split('\n')[0]
                
                text_el = tweet.find_element(By.CSS_SELECTOR, "[data-testid='tweetText']")
                msg_text = get_element_text_with_emojis(driver, text_el)
                
                if user_text or msg_text:
                    comments.append((user_text, msg_text))
            except:
                pass
    except Exception as e:
        print(f"  [Comment Scraper] X parse error: {e}")
    return comments

def save_comments(publisher_name, platform, resolved_url, comments, stream_title=""):
    """Saves comments to local CSV file and appends them in batch to Google Sheets 'comments' tab."""
    display_platform = {
        "youtube": "YouTube",
        "facebook": "Facebook",
        "tiktok": "TikTok",
        "x": "X"
    }.get(platform.lower(), platform)
    now = get_ict_now()
    now_str = f"{now.day}/{now.month}/{now.year}, {now.strftime('%H:%M:%S')}"
    
    comments_csv = "live_comments.csv"
    csv_exists = os.path.exists(comments_csv)
    
    # 1. Load recent comments from CSV for deduplication
    recent_set = load_recent_saved_comments(comments_csv)
    
    # Filter comments that haven't been saved yet
    new_comments = []
    for author, text in comments:
        # Check against lowercased/stripped values to ensure robust deduplication
        key = (platform.lower(), resolved_url.strip(), author.strip(), text.strip())
        if key not in recent_set:
            new_comments.append((author, text))
            recent_set.add(key)
            
    if not new_comments:
        print(f"  [Comment Scraper] All {len(comments)} comments are duplicates. Skipping save.")
        return
        
    # 2. Save to local CSV
    try:
        with open(comments_csv, mode="a", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            if not csv_exists:
                writer.writerow(["Timestamp (ICT)", "Channel", "Platform", "Live URL", "Title", "Username", "Comment Text"])
            for author, text in new_comments:
                writer.writerow([now_str, publisher_name, display_platform, resolved_url, stream_title, author, text])
        print(f"  [Comment Scraper] Saved {len(new_comments)} new comments to {comments_csv}.")
    except Exception as e:
        print(f"  [Comment Scraper] Error saving comments to CSV: {e}")
        
    # 3. Save to Google Sheets
    if not TOKEN_PATH or not os.path.exists(TOKEN_PATH):
        print("  [Comment Scraper] Warning: token.json not found. Skipping Google Sheets comments update.")
        return
        
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        
        # Open or dynamically create the worksheet tab named 'comments'
        try:
            sheet = spreadsheet.worksheet("comments")
            # Migration check: if sheet exists but uses old 6-column layout, shift columns
            try:
                headers = sheet.row_values(1)
                if headers and len(headers) == 6 and headers[4] == "Username":
                    print("  [Comment Scraper] Migrating 'comments' worksheet to 7-column schema...")
                    sheet.update("A1:G1", [["Timestamp (ICT)", "Channel", "Platform", "Live URL", "Title", "Username", "Comment Text"]])
                    all_vals = sheet.get_all_values()
                    if len(all_vals) > 1:
                        new_rows = []
                        for row in all_vals[1:]:
                            if len(row) >= 6:
                                new_row = row[:4] + [""] + row[4:6]
                                new_rows.append(new_row)
                        last_row = len(all_vals)
                        sheet.batch_clear([f"A2:G{last_row}"])
                        sheet.update(f"A2:G{last_row}", new_rows)
                        print("  [Comment Scraper] 'comments' worksheet migrated successfully!")
            except Exception as migration_err:
                print(f"  [Comment Scraper] Warning during sheet migration check: {migration_err}")
        except gspread.exceptions.WorksheetNotFound:
            print("  [Comment Scraper] Worksheet 'comments' not found. Creating a new one...")
            sheet = spreadsheet.add_worksheet(title="comments", rows="2000", cols="7")
            sheet.append_row(["Timestamp (ICT)", "Channel", "Platform", "Live URL", "Title", "Username", "Comment Text"])
            
        # Ensure headers are present
        try:
            first_cell = sheet.acell("A1").value
            if not first_cell:
                sheet.append_row(["Timestamp (ICT)", "Channel", "Platform", "Live URL", "Title", "Username", "Comment Text"])
        except:
            pass
            
        # Format rows for bulk insert
        values_to_append = []
        for author, text in new_comments:
            values_to_append.append([now_str, publisher_name, display_platform, resolved_url, stream_title, author, text])
            
        print(f"  [Comment Scraper] Appending {len(values_to_append)} rows to Google Sheet ('comments' sheet)...")
        
        # Check sheet rows size warning
        try:
            row_count = len(sheet.get_all_values())
            if row_count > 100000:
                print(f"  [Comment Scraper] WARNING: 'comments' worksheet has {row_count} rows. Consider creating a new tab.")
        except:
            pass
            
        sheet.append_rows(values_to_append)
        print("  [Comment Scraper] Google Sheet 'comments' tab successfully updated!")
    except Exception as e:
        print(f"  [Comment Scraper] Error updating Google Sheet comments: {e}")

def scrape_comments_for_stream(driver, publisher_name, platform, resolved_url, stream_title=""):
    """Helper to run the comment scrape on the designated live stream URL for the configured duration."""
    if platform.lower() == "facebook":
        try:
            match = re.search(r'/videos/(?:[^/]+/)*(\d+)', resolved_url) or re.search(r'[?&]v=(\d+)', resolved_url) or re.search(r'/live/(?:[^/]+/)*(\d+)', resolved_url)
            if match:
                video_id = match.group(1)
                optimized_url = f"https://www.facebook.com/watch/?v={video_id}"
                if resolved_url != optimized_url:
                    print(f"  [Comment Scraper] Automatically optimized Facebook URL to watch layout: {optimized_url}")
                    resolved_url = optimized_url
        except Exception as opt_err:
            print(f"  [Comment Scraper] Warning: Failed to optimize Facebook URL: {opt_err}")

    print(f"  [Comment Scraper] Scraping comments for {publisher_name} on {platform.upper()} (URL: {resolved_url}) for {COMMENT_DURATION} seconds...")
    try:
        def force_click(el):
            click_script = """
            var el = arguments[0];
            var mousedown = new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window});
            var mouseup = new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window});
            var click = new MouseEvent('click', {bubbles: true, cancelable: true, view: window});
            el.dispatchEvent(mousedown);
            el.dispatchEvent(mouseup);
            el.dispatchEvent(click);
            """
            driver.execute_script(click_script, el)

        try:
            driver.get(resolved_url)
        except Exception as get_err:
            if "timeout" in str(get_err).lower():
                print(f"  [Comment Scraper] Warning: page load timed out for {resolved_url}, but attempting to parse comments anyway...")
            else:
                raise get_err
        
        all_comments = []
        seen_comments = set()
        
        if platform.lower() == "facebook":
            time.sleep(5)
            # Try clicking potential video expanders/play buttons to start the video and enter watch mode
            try:
                # 1. Look for the exact transparent video overlay class you clicked
                target_selector = "div.x1ey2m1c.x9f619.xtijo5x.x1o0tod.x10l6tqk.x13vifvy.x1ypdohk"
                targets = driver.find_elements(By.CSS_SELECTOR, target_selector)
                
                # If not found, use a slightly more generic overlay selector
                if not targets:
                    targets = driver.find_elements(By.CSS_SELECTOR, "div.x1ey2m1c.xtijo5x")
                if not targets:
                    targets = driver.find_elements(By.CSS_SELECTOR, "div.xtijo5x")
                
                clicked_overlay = False
                for target in targets:
                    try:
                        if target.is_displayed():
                            print("  [Comment Scraper] Clicking direct video player overlay...")
                            force_click(target)
                            time.sleep(3)
                            clicked_overlay = True
                            break
                    except:
                        pass
                
                # 2. Recheck if comments are visible. If still not visible, look for play buttons as a fallback
                comments = driver.find_elements(By.XPATH, "//*[@role='article']")
                if not comments:
                    print("  [Comment Scraper] Comments still not visible. Trying general play button overlays...")
                    play_buttons = []
                    player_containers = driver.find_elements(By.XPATH, "//*[@aria-label='Video player' or @aria-label='ผู้เล่นวิดีโอ']")
                    for player in player_containers:
                        play_buttons.extend(player.find_elements(By.XPATH, ".//*[@role='button']"))
                    play_buttons.extend(driver.find_elements(By.XPATH, "//div[@role='button'][@aria-label='เล่นวิดีโอ' or @aria-label='Play' or @aria-label='Play video' or @aria-label='Watch' or @aria-label='รับชมสด']"))
                    
                    for btn in play_buttons:
                        try:
                            if btn.is_displayed():
                                print("  [Comment Scraper] Clicking play button overlay...")
                                force_click(btn)
                                time.sleep(3)
                                break
                        except:
                            pass
            except Exception as click_err:
                print(f"  [Comment Scraper] Warning during video player initialization: {click_err}")
                
            try:
                print("  [Comment Scraper] Attempting to set Facebook comment sort to 'All Comments'...")
                sort_dropdowns = driver.find_elements(By.CSS_SELECTOR, ".xe0p6wg > div:nth-child(1) > span:nth-child(1)")
                if not sort_dropdowns:
                    sort_dropdowns = driver.find_elements(By.CSS_SELECTOR, ".xe0p6wg")
                if not sort_dropdowns:
                    sort_dropdowns = driver.find_elements(By.XPATH, "//*[contains(text(), 'Most Relevant') or contains(text(), 'เกี่ยวข้องที่สุด') or contains(text(), 'Top Comments')]")
                
                if sort_dropdowns:
                    force_click(sort_dropdowns[0])
                    time.sleep(1.5)
                    priority_keywords = [
                        "Newest", "ล่าสุด", 
                        "Live Comments", "ความคิดเห็นสด"
                    ]
                    xpath_opts = " or ".join([f"text()='{k}'" for k in priority_keywords])
                    xpath_query = f"//*[{xpath_opts}]"
                    options = driver.find_elements(By.XPATH, xpath_query)
                    
                    if not options:
                        fallback_keywords = [
                            "All Comments", "ความคิดเห็นทั้งหมด"
                        ]
                        xpath_opts = " or ".join([f"text()='{k}'" for k in fallback_keywords])
                        xpath_query = f"//*[{xpath_opts}]"
                        options = driver.find_elements(By.XPATH, xpath_query)
                        
                    if options:
                        selected_text = options[0].text or "Newest/All"
                        force_click(options[0])
                        print(f"  [Comment Scraper] Successfully selected '{selected_text}' sort order.")
                        time.sleep(2)
                    else:
                        print("  [Comment Scraper] Warning: Sort menu options not found.")
                else:
                    comments = driver.find_elements(By.XPATH, "//*[@role='article']")
                    if comments:
                        print("  [Comment Scraper] Facebook comment sort dropdown not found, but comments are visible (assuming Live Chat mode).")
                    else:
                        print("  [Comment Scraper] Warning: Facebook comment sort dropdown trigger not found.")
            except Exception as sort_err:
                print(f"  [Comment Scraper] Warning: Failed to set Facebook comment sort order: {sort_err}")
        
        # Check if persistent profile is enabled to determine refresh interval
        use_persistent = False
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    use_persistent = cfg.get("use_persistent_chrome_profile", False)
        except:
            pass

        refresh_interval = 60 if use_persistent else 15
        
        start_time = time.time()
        end_time = start_time + COMMENT_DURATION
        plat_lower = platform.lower()
        last_refresh_time = start_time
        
        if plat_lower == "facebook":
            if use_persistent:
                print("  [Comment Scraper] Persistent profile is active. Real-time comments should stream automatically.")
            else:
                print("  [Comment Scraper] Running in anonymous mode. Will refresh page periodically to pull comments.")
        
        while time.time() < end_time:
            if plat_lower == "facebook" and time.time() - last_refresh_time > refresh_interval:
                print("  [Comment Scraper] Refreshing Facebook page to pull new comments...")
                try:
                    driver.refresh()
                    time.sleep(5)
                    print("  [Comment Scraper] Re-applying 'Newest' sort order...")
                    sort_dropdowns = driver.find_elements(By.CSS_SELECTOR, ".xe0p6wg > div:nth-child(1) > span:nth-child(1)")
                    if not sort_dropdowns:
                        sort_dropdowns = driver.find_elements(By.CSS_SELECTOR, ".xe0p6wg")
                    if not sort_dropdowns:
                        sort_dropdowns = driver.find_elements(By.XPATH, "//*[contains(text(), 'Most Relevant') or contains(text(), 'เกี่ยวข้องที่สุด') or contains(text(), 'Top Comments')]")
                    
                    if sort_dropdowns:
                        force_click(sort_dropdowns[0])
                        time.sleep(1.5)
                        priority_keywords = [
                            "Newest", "ล่าสุด", 
                            "Live Comments", "ความคิดเห็นสด"
                        ]
                        xpath_opts = " or ".join([f"text()='{k}'" for k in priority_keywords])
                        xpath_query = f"//*[{xpath_opts}]"
                        options = driver.find_elements(By.XPATH, xpath_query)
                        
                        if not options:
                            fallback_keywords = [
                                "All Comments", "ความคิดเห็นทั้งหมด"
                            ]
                            xpath_opts = " or ".join([f"text()='{k}'" for k in fallback_keywords])
                            xpath_query = f"//*[{xpath_opts}]"
                            options = driver.find_elements(By.XPATH, xpath_query)
                            
                        if options:
                            selected_text = options[0].text or "Newest/All"
                            force_click(options[0])
                            print(f"  [Comment Scraper] Successfully selected '{selected_text}' sort order.")
                            time.sleep(2)
                    else:
                        comments = driver.find_elements(By.XPATH, "//*[@role='article']")
                        if comments:
                            print("  [Comment Scraper] Facebook comment sort dropdown not found after refresh, but comments are visible (assuming Live Chat mode).")
                        else:
                            print("  [Comment Scraper] Warning: Facebook comment sort dropdown trigger not found after refresh.")
                except Exception as ref_err:
                    print(f"  [Comment Scraper] Warning during Facebook refresh: {ref_err}")
                last_refresh_time = time.time()
                
            comments = []
            if plat_lower == "youtube":
                comments = scrape_youtube_comments_live(driver)
            elif plat_lower == "facebook":
                comments = scrape_facebook_comments_live(driver)
            elif plat_lower == "tiktok":
                comments = scrape_tiktok_comments_live(driver)
            elif plat_lower == "x":
                comments = scrape_x_comments_live(driver)
                
            # Add unique comments to session accumulator
            for author, text in comments:
                # Clean up whitespaces
                a_clean = author.strip()
                t_clean = text.strip()
                key = (a_clean, t_clean)
                if key not in seen_comments:
                    seen_comments.add(key)
                    all_comments.append((author, text))
            
            # Calculate sleep time to not overshoot target duration
            time_left = end_time - time.time()
            if time_left <= 0:
                break
            time.sleep(min(5, time_left))
            
        if all_comments:
            save_comments(publisher_name, platform, resolved_url, all_comments, stream_title)
        else:
            print(f"  [Comment Scraper] No live comments could be scraped for {publisher_name} on {platform.upper()}.")
    except Exception as e:
        print(f"  [Comment Scraper] Error scraping URL {resolved_url}: {e}")

def check_view_count_task(publisher_name, platform, url, stream_type, topic_keywords):
    """Isolated task running in parallel to check a single platform livestream view count."""
    print(f"  [Checking] Starting view count check: {publisher_name} ({platform.upper()}) -> {url}")
    driver = setup_stealth_driver()
    try:
        count, title, resolved_url = 0, "", url
        if platform == "facebook":
            count, title, resolved_url = scrape_facebook_live(driver, url, topic_keywords)
        elif platform == "youtube":
            yt_keywords = ["อนาคตเมืองหลวง"] if publisher_name == "Thairath" else topic_keywords
            count, title, resolved_url = scrape_youtube_live(driver, url, yt_keywords)
        elif platform == "tiktok":
            count, title, resolved_url = scrape_tiktok_live(driver, url, topic_keywords)
        elif platform == "x":
            count, title, resolved_url = scrape_x_live(driver, url, topic_keywords)
            
        if not resolved_url:
            resolved_url = url
            
        return publisher_name, platform, url, count, title, resolved_url, stream_type
    except Exception as e:
        print(f"  Error checking view count for {publisher_name} on {platform.upper()}: {e}")
        return publisher_name, platform, url, 0, "", url, stream_type
    finally:
        try:
            driver.quit()
        except:
            pass

def scrape_comments_standalone(publisher_name, platform, resolved_url, stream_title=""):
    """Spawns an isolated webdriver to scrape comments for a single active stream in parallel."""
    driver = setup_stealth_driver()
    try:
        scrape_comments_for_stream(driver, publisher_name, platform, resolved_url, stream_title)
    except Exception as e:
        print(f"  [Comment Scraper] Error in standalone comments scraping: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass

def get_ict_now():
    return datetime.now(ICT_TZ)

def setup_stealth_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    # Force language to English for consistent selector text
    chrome_options.add_argument("--lang=en-US")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--mute-audio")
    chrome_options.page_load_strategy = 'eager'
    
    # Load config dynamically inside setup_stealth_driver to respect active persistent profile settings
    use_persistent = False
    dst_profile = None
    src_profile = None
    try:
        config_path = os.path.join(EXTERNAL_DIR, "config.json")
        if os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                use_persistent = cfg.get("use_persistent_chrome_profile", False)
    except Exception as e:
        print(f"Warning: Failed to read use_persistent_chrome_profile setting from config.json: {e}")

    if use_persistent:
        import shutil
        import threading
        
        base_dir = EXTERNAL_DIR
        src_profile = os.path.join(base_dir, "chrome_profile", "MainThread")
        
        thread_name = threading.current_thread().name
        safe_thread_name = re.sub(r'[^a-zA-Z0-9_-]', '_', thread_name)
        dst_profile = os.path.join(base_dir, "chrome_profile", f"temp_{safe_thread_name}")
        
        # Ensure source profile exists
        if not os.path.exists(src_profile):
            try:
                os.makedirs(src_profile, exist_ok=True)
            except Exception as e:
                print(f"Warning: Could not create source profile directory: {e}")
            
        # Copy src_profile to dst_profile
        if os.path.exists(dst_profile):
            try:
                shutil.rmtree(dst_profile)
            except Exception as e:
                print(f"Warning: Failed to clean old temp profile {dst_profile}: {e}")
                
        try:
            os.makedirs(dst_profile, exist_ok=True)
            
            # Fast copy excluding massive caching/storage directories
            exclude_dirs = {
                "Cache", "Code Cache", "GPUCache", "Session Storage", "IndexedDB", 
                "Service Worker", "Crashpad", "databases", "File System", "blob_storage"
            }
            
            def ignore_patterns(path, names):
                ignored = []
                for name in names:
                    if name in exclude_dirs or name.startswith("Cache") or name.endswith(".tmp") or name in ["lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"]:
                        ignored.append(name)
                return ignored
                
            shutil.copytree(src_profile, dst_profile, ignore=ignore_patterns, dirs_exist_ok=True)
            chrome_options.add_argument(f"--user-data-dir={dst_profile}")
            print(f"  [Chrome Profile] Using copied persistent profile in thread '{thread_name}' -> {dst_profile}")
        except Exception as copy_err:
            print(f"Warning: Failed to copy persistent profile: {copy_err}. Falling back to default session.")

    driver = webdriver.Chrome(options=chrome_options)
    register_driver(driver)
    driver.set_page_load_timeout(20)
    
    # Clean up copied profile directory on quit
    if use_persistent and dst_profile and dst_profile != src_profile:
        original_quit = driver.quit
        def custom_quit():
            try:
                original_quit()
            finally:
                if os.path.exists(dst_profile):
                    for _ in range(5):
                        try:
                            import shutil
                            shutil.rmtree(dst_profile)
                            break
                        except Exception as e:
                            time.sleep(0.2)
        driver.quit = custom_quit

    
    # Block ad networks and tracking endpoints via CDP to save bandwidth and speed up page load
    try:
        driver.execute_cdp_cmd('Network.enable', {})
        ad_domains = [
            '*google-analytics.com*', '*doubleclick.net*', '*googlesyndication.com*',
            '*adnxs.com*', '*taboola.com*', '*outbrain.com*', '*adsystem*',
            '*adservice*', '*googleadservices*', '*facebook.com/tr*',
            '*pubmatic.com*', '*criteo.com*', '*rubiconproject.com*', '*casalemedia.com*',
            '*openx.net*', '*adtech*', '*adform*', '*smartadserver*', '*adroll*',
            # TikTok/ByteDance telemetry and analytics endpoints
            '*byteoversea.com*', 
            '*tiktok.com/api/v1/web/report*', '*tiktok.com/share/analytics*',
            '*tiktok.com/api/feedback*', '*tiktok.com/api/v1/feedback*',
            '*tiktok.com/api/report*'
        ]
        driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls': ad_domains})
    except Exception as e:
        print(f"Warning: Failed to set ad blocker: {e}")
        
    return driver

def clean_and_parse_count(text):
    """Parses text like '1.2K watching', '185K views', '3,456', '5.4 พัน' into an integer."""
    if not text:
        return 0
    # Remove all spaces and newlines to join split digits
    cleaned = re.sub(r'\s+', '', text).lower()
    cleaned = cleaned.replace("พัน", "k").replace("ล้าน", "m")
    
    # Extract matching digits and potential k/m suffix
    match = re.search(r'([\d,.]+)\s*([km]?)', cleaned)
    if match:
        val_str = match.group(1).replace(',', '')
        suffix = match.group(2)
        try:
            val = float(val_str)
            if suffix == 'k':
                return int(val * 1000)
            elif suffix == 'm':
                return int(val * 1000000)
            else:
                return int(val)
        except ValueError:
            return 0
    return 0

# --- PLATFORM SCRAPERS ---

def check_is_youtube_upcoming(driver):
    """Checks if the currently loaded YouTube watch page is an upcoming/scheduled stream (not active live yet)."""
    try:
        # Check if Google captcha/unusual traffic block page is displayed
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "unusual traffic" in body_text.lower() or "ระบบของเราตรวจพบการเข้าชมที่ผิดปกติ" in body_text:
                print("  [YouTube Active Check] WARNING: YouTube Captcha/Unusual Traffic block detected!")
                return False
        except:
            pass

        # Check 1: visible standby/waiting player overlay elements
        upcoming_elements = driver.find_elements(By.CSS_SELECTOR, ".ytp-upcoming-event-label, .ytp-offline-slate, .ytp-scheduled-slate")
        for el in upcoming_elements:
            if el.is_displayed():
                print(f"  [YouTube Active Check] Found visible upcoming/standby player element: {el.text}")
                return True
                
        # Check 2: ytInitialPlayerResponse JSON check
        try:
            src = driver.page_source
            match = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", src)
            if not match:
                match = re.search(r"var ytInitialPlayerResponse\s*=\s*({.+?});", src)
            if match:
                data = json.loads(match.group(1))
                playability = data.get("playabilityStatus", {})
                status = playability.get("status")
                # LIVE_STREAM_OFFLINE is the status when the stream hasn't started yet
                if status == "LIVE_STREAM_OFFLINE":
                    print("  [YouTube Active Check] Confirmed upcoming/scheduled live stream via playabilityStatus: LIVE_STREAM_OFFLINE")
                    return True
        except Exception as e:
            print(f"  [YouTube Active Check] JSON check error: {e}")
                    
    except Exception as e:
        print(f"  [YouTube Active Check] Error checking active status: {e}")
    return False

def check_is_youtube_active_live(driver):
    """Verifies if the currently loaded YouTube watch page is an active live stream."""
    try:
        # Check if Google captcha/unusual traffic block page is displayed
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "unusual traffic" in body_text.lower() or "ระบบของเราตรวจพบการเข้าชมที่ผิดปกติ" in body_text:
                print("  [YouTube Active Live Check] WARNING: YouTube Captcha/Unusual Traffic block detected!")
                return False
        except:
            pass

        # Check 1: ytInitialPlayerResponse JSON check (extremely fast and robust)
        try:
            src = driver.page_source
            match = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", src)
            if not match:
                match = re.search(r"var ytInitialPlayerResponse\s*=\s*({.+?});", src)
            if match:
                data = json.loads(match.group(1))
                playability = data.get("playabilityStatus", {}) or {}
                status = playability.get("status")
                video_details = data.get("videoDetails", {}) or {}
                is_live = video_details.get("isLive", False)
                
                # Check playability status and isLive flag
                if status == "OK" and is_live == True:
                    print("  [YouTube Active Live Check] Confirmed active live stream via videoDetails.isLive.")
                    return True
                elif status == "OK" and is_live == False:
                    print("  [YouTube Active Live Check] Confirmed ended stream or normal video via videoDetails.isLive being False.")
                    return False
        except Exception as e:
            print(f"  [YouTube Active Live Check] JSON check error: {e}")

        # Check 2: current metadata text (fallback)
        metadata_area = driver.find_elements(By.CSS_SELECTOR, "ytd-watch-metadata, #info-contents, #info")
        for area in metadata_area:
            text = area.text.lower()
            # If it has ended live indicators, it is not active
            if "streamed" in text or "แพร่ภาพสดเมื่อ" in text:
                print("  [YouTube Active Live Check] Detected ended stream indicator.")
                return False
                
            # If it has active indicators
            if "watching" in text or "กำลังรับชม" in text or "ผู้ชม" in text:
                print("  [YouTube Active Live Check] Confirmed active live stream via metadata text.")
                return True
                    
        # Check 3: page source for active flags on player (fallback)
        player = driver.find_elements(By.CSS_SELECTOR, "#movie_player")
        for p in player:
            classes = p.get_attribute("class") or ""
            if "ytp-live" in classes.split():
                print("  [YouTube Active Live Check] ytp-live class found on player.")
                return True
                
    except Exception as e:
        print(f"  [YouTube Active Live Check] Error checking active status: {e}")
        
    return False

def scrape_youtube_live(driver, channel_streams_urls, topic_keywords):
    """Scrapes YouTube Live view count. Returns (count, title)."""
    if isinstance(channel_streams_urls, str):
        channel_streams_urls = [channel_streams_urls]
        
    direct_video_url = None
    for url in channel_streams_urls:
        if "/watch" in url or "/live/" in url or "youtu.be/" in url:
            direct_video_url = url
            break
            
    if direct_video_url:
        target_url = direct_video_url
        print(f"  [YouTube] Loading direct live stream URL: {target_url}")
        try:
            driver.get(target_url)
            time.sleep(5)
            target_title = driver.title or "YouTube Live"
            if target_title.endswith(" - YouTube"):
                target_title = target_title[:-10]
                
            if check_is_youtube_active_live(driver):
                # Confirmed active live stream! Proceed.
                pass
            elif check_is_youtube_upcoming(driver):
                print(f"  [YouTube] Live stream is not active yet (upcoming/scheduled): {target_url}")
                return 0, target_title, target_url
            else:
                print(f"  [YouTube] Video is not currently live (ended live stream or normal video): {target_url}")
                return 0, target_title, target_url
        except Exception as e:
            print(f"  [YouTube] Error loading direct stream page: {e}")
            return 0, "", target_url
    else:
        all_active_lives = []
        for url in channel_streams_urls:
            clean_url = url.split('?')[0].rstrip('/')
            if not clean_url.endswith("/streams"):
                clean_url = clean_url + "/streams"
                
            print(f"  [YouTube] Loading streams page: {clean_url}")
            try:
                driver.get(clean_url)
                time.sleep(5)
                
                # Collect links and texts
                a_tags = driver.find_elements(By.TAG_NAME, "a")
                watch_links = {}
                
                for a in a_tags:
                    try:
                        href = a.get_attribute("href")
                        text = a.text.strip()
                        if href and "/watch?v=" in href:
                            # Resolve to clean URL without parameters
                            clean_href = href.split('&')[0]
                            if clean_href not in watch_links:
                                watch_links[clean_href] = []
                            if text:
                                watch_links[clean_href].append(text)
                    except:
                        pass
                        
                # Find active live streams
                for watch_url, texts in watch_links.items():
                    is_live = False
                    title = ""
                    for t in texts:
                        t_clean = t.strip()
                        if t_clean.upper() in ["LIVE", "🔴LIVE", "🔴 LIVE"] or t_clean in ["สด", "🔴สด", "🔴 สด"]:
                            is_live = True
                        else:
                            if len(t) > len(title):
                                title = t
                    if is_live:
                        all_active_lives.append({"url": watch_url, "title": title})
                        
            except Exception as e:
                print(f"  [YouTube] Error loading streams page {url}: {e}")
                
        print(f"  [YouTube] Found {len(all_active_lives)} active live streams across all channels.")
        for live in all_active_lives:
            print(f"    - Title: {live['title']} ({live['url']})")
            
        if not all_active_lives:
            return 0, "", ""
            
        # Select matching stream
        target_live = None
        for live in all_active_lives:
            if any(k.lower() in live["title"].lower() for k in topic_keywords):
                target_live = live
                break
                
        if not target_live:
            print("  [YouTube] No live stream matched topic keywords. Returning 0.")
            return 0, "", ""
            
        target_title = target_live["title"]
        target_url = target_live["url"]
        
        # Navigate to target live stream
        print(f"  [YouTube] Loading live stream: {target_url}")
        try:
            driver.get(target_url)
            time.sleep(5)
            if check_is_youtube_active_live(driver):
                # Confirmed active live stream! Proceed.
                pass
            elif check_is_youtube_upcoming(driver):
                print(f"  [YouTube] Live stream is not active yet (upcoming/scheduled): {target_url}")
                return 0, target_title, target_url
            else:
                print(f"  [YouTube] Video is not currently live (ended live stream or normal video): {target_url}")
                return 0, target_title, target_url
        except Exception as e:
            print(f"  [YouTube] Error loading live stream details: {e}")
            return 0, target_title, target_url

    try:
        src = driver.page_source
        
        # Method 1: Check liveStreamingDetails in player response (for concurrent viewers)
        match = re.search(r"ytInitialPlayerResponse\s*=\s*({.+?});", src)
        if not match:
            match = re.search(r"var ytInitialPlayerResponse\s*=\s*({.+?});", src)
        if match:
            try:
                data = json.loads(match.group(1))
                lsd = data.get("liveStreamingDetails", {})
                concurrent = lsd.get("concurrentViewers")
                if concurrent:
                    val = int(concurrent)
                    print(f"  [YouTube] Found concurrent viewers in liveStreamingDetails: {val}")
                    return val, target_title, target_url
            except Exception as e:
                print(f"  [YouTube] Error parsing player JSON: {e}")
                
        # Method 2: Check ytInitialData (which has concurrent viewers in runs)
        match_data = re.search(r"ytInitialData\s*=\s*({.+?});", src)
        if not match_data:
            match_data = re.search(r"var ytInitialData\s*=\s*({.+?});", src)
        if match_data:
            try:
                data2 = json.loads(match_data.group(1))
                
                # Check videoPrimaryInfoRenderer
                try:
                    contents = data2["contents"]["twoColumnWatchNextResults"]["results"]["results"]["contents"]
                    primary_info = contents[0]["videoPrimaryInfoRenderer"]
                    runs = primary_info["viewCount"]["videoViewCountRenderer"]["viewCount"]["runs"]
                    combined_text = "".join([r.get("text", "") for r in runs])
                    if "watching" in combined_text.lower() or "ผู้ชม" in combined_text or "คนดู" in combined_text:
                        val = clean_and_parse_count(combined_text)
                        if val > 0:
                            print(f"  [YouTube] Found concurrent viewers in ytInitialData primaryInfo: {val}")
                            return val, target_title, target_url
                except:
                    pass
                    
                # Check playerOverlays
                try:
                    overlay = data2["playerOverlays"]["playerOverlayRenderer"]
                    runs = overlay["videoDetails"]["playerOverlayVideoDetailsRenderer"]["subtitle"]["runs"]
                    combined_text = "".join([r.get("text", "") for r in runs])
                    if "watching" in combined_text.lower() or "ผู้ชม" in combined_text or "คนดู" in combined_text:
                        val = clean_and_parse_count(combined_text)
                        if val > 0:
                            print(f"  [YouTube] Found concurrent viewers in ytInitialData overlay: {val}")
                            return val, target_title, target_url
                except:
                    pass
            except Exception as e:
                print(f"  [YouTube] Error parsing ytInitialData: {e}")
                
        # Method 3: DOM search for elements
        xpath_query = ".//*[contains(text(), 'watching') or contains(text(), 'ผู้ชม') or contains(text(), 'คนดู')]"
        for container_selector in ["ytd-watch-metadata", "#info", "#movie_player", "#player"]:
            try:
                container = driver.find_element(By.CSS_SELECTOR, container_selector)
                elements = container.find_elements(By.XPATH, xpath_query)
                for el in elements:
                    try:
                        t = el.text.strip()
                        if t and ("watching" in t.lower() or "ผู้ชม" in t or "คนดู" in t):
                            val = clean_and_parse_count(t)
                            if val > 0:
                                print(f"  [YouTube] Found view count in DOM: {val} (text: '{t}')")
                                return val, target_title, target_url
                    except:
                        pass
            except:
                pass
                
    except Exception as e:
        print(f"  [YouTube] Error scraping live stream details: {e}")
    return 0, target_title, target_url

def scrape_facebook_live(driver, channel_live_urls, topic_keywords):
    """Scrapes Facebook Live view count by loading the public plugins video player to bypass login walls. Returns (count, title)."""
    if isinstance(channel_live_urls, str):
        channel_live_urls = [channel_live_urls]
        
    for v_url in channel_live_urls:
        embed_url = f"https://www.facebook.com/plugins/video.php?href={v_url}&show_text=true"
        print(f"  [Facebook] Loading plugins embed URL: {embed_url}")
        try:
            driver.get(embed_url)
            time.sleep(5)
            
            # Dismiss the login dialog if one displays inside the iframe
            try:
                dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                if dialogs:
                    for xpath in ["//div[@role='dialog']//div[@aria-label='Close']", "//div[@role='dialog']//div[@aria-label='ปิด']", "//div[@aria-label='Close']", "//div[@aria-label='ปิด']"]:
                        try:
                            close_btn = driver.find_element(By.XPATH, xpath)
                            if close_btn.is_displayed():
                                close_btn.click()
                                time.sleep(1)
                                break
                        except:
                            pass
            except:
                pass
                
            # Check if active live classes are present in the embed player
            is_live_video = False
            live_indicators = driver.find_elements(By.CSS_SELECTOR, "._u_g, ._u_h")
            for el in live_indicators:
                if el.is_displayed():
                    is_live_video = True
                    break
                    
            # Get video title from player details
            title = ""
            try:
                title_elements = driver.find_elements(By.CSS_SELECTOR, "._4i-s, ._5pbx, h1, h2")
                for el in title_elements:
                    t = el.text.strip()
                    if t:
                        title = t
                        break
            except:
                pass
                
            if not title:
                title = driver.title or "Facebook Video"
            if title.endswith(" | Facebook"):
                title = title[:-11]
            elif title.endswith(" - Facebook"):
                title = title[:-11]
                
            if not is_live_video:
                print(f"  [Facebook] Video is not currently live (offline/ended).")
                return 0, title, v_url
                
            # Find concurrent viewers
            view_val = 0
            try:
                viewer_elements = driver.find_elements(By.CSS_SELECTOR, "._u_l")
                for el in viewer_elements:
                    if el.is_displayed():
                        t = el.text.strip()
                        if t:
                            v = clean_and_parse_count(t)
                            if v > 0:
                                view_val = v
                                break
            except:
                pass
                
            # Fallback to body text match
            if view_val == 0:
                try:
                    body_text = driver.find_element(By.TAG_NAME, "body").text
                    match = re.search(r'(?:LIVE|สด)\s*([\d.,KkMmพันล้าน]+)', body_text)
                    if match:
                        v = clean_and_parse_count(match.group(1))
                        if v > 0:
                            view_val = v
                except:
                    pass
                    
            if view_val > 0:
                print(f"  [Facebook] Found viewers: {title} ({view_val} viewers)")
                return view_val, title, v_url
            else:
                print(f"  [Facebook] Could not find viewer count on embed page. Returning 0.")
                return 0, title, v_url
                
        except Exception as e:
            print(f"  [Facebook] Error loading URL {v_url}: {e}")
            
    return 0, "", ""

def upload_screenshot_to_google_drive(creds, filepath, filename):
    """Uploads a screenshot to Google Drive in the folder 'LIVE Project screenshots'."""
    try:
        from google.auth.transport.requests import Request
        import requests
        
        # Ensure credentials are valid
        if not creds.valid:
            creds.refresh(Request())
            
        headers = {
            "Authorization": f"Bearer {creds.token}"
        }
        
        # 1. Search for the folder named "LIVE Project screenshots"
        folder_id = None
        search_url = "https://www.googleapis.com/drive/v3/files"
        query = "name = 'LIVE Project screenshots' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        params = {
            "q": query,
            "fields": "files(id)"
        }
        r_search = requests.get(search_url, headers=headers, params=params, timeout=10)
        if r_search.status_code == 200:
            files = r_search.json().get("files", [])
            if files:
                folder_id = files[0]["id"]
                print(f"  [Google Drive] Found existing folder '{folder_id}'")
                
        # 2. If folder not found, create it
        if not folder_id:
            print("  [Google Drive] Folder 'LIVE Project screenshots' not found. Creating it...")
            create_url = "https://www.googleapis.com/drive/v3/files"
            folder_metadata = {
                "name": "LIVE Project screenshots",
                "mimeType": "application/vnd.google-apps.folder"
            }
            r_create = requests.post(create_url, headers=headers, json=folder_metadata, timeout=10)
            if r_create.status_code == 200:
                folder_id = r_create.json().get("id")
                print(f"  [Google Drive] Created folder with ID: {folder_id}")
                
        # 3. Upload the file to the folder
        upload_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
        metadata = {
            "name": filename,
            "parents": [folder_id] if folder_id else []
        }
        
        files = {
            "metadata": (None, json.dumps(metadata), "application/json; charset=UTF-8"),
            "file": (filename, open(filepath, "rb"), "image/png")
        }
        
        r_upload = requests.post(upload_url, headers=headers, files=files, timeout=20)
        if r_upload.status_code == 200:
            file_id = r_upload.json().get("id")
            print(f"  [Google Drive] Uploaded screenshot. File ID: {file_id}")
            
            # Make the file public (anyone with the link can view it)
            perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
            perm_data = {
                "role": "reader",
                "type": "anyone"
            }
            requests.post(perm_url, headers=headers, json=perm_data, timeout=10)
            
            # Get the webViewLink
            get_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
            r_get = requests.get(get_url, headers=headers, params={"fields": "webViewLink"}, timeout=10)
            if r_get.status_code == 200:
                return r_get.json().get("webViewLink")
                
            return f"https://drive.google.com/file/d/{file_id}/view"
        else:
            print(f"  [Google Drive] Upload failed: {r_upload.status_code} {r_upload.text}")
    except Exception as e:
        print(f"  [Google Drive] Error uploading screenshot: {e}")
    return None

def check_is_tiktok_captcha(driver):
    """Detects if TikTok has triggered a Captcha / Security verification block."""
    try:
        # Check elements by CSS selector
        selectors = [
            "[id*='captcha']", "[class*='captcha']", 
            "[id*='verify']", "[class*='verify']",
            "iframe[src*='verify']", "iframe[src*='captcha']",
            ".sec-sdk-iframe", "#sec-sdk-iframe",
            "#tiktok-verify-page", ".tiktok-verify-page"
        ]
        for sel in selectors:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in elements:
                    if el.is_displayed():
                        # Verify it's actually a verification widget
                        tag_name = el.tag_name.lower()
                        class_attr = (el.get_attribute("class") or "").lower()
                        id_attr = (el.get_attribute("id") or "").lower()
                        if tag_name == "iframe" or "container" in class_attr or "wrap" in class_attr or "captcha" in class_attr or "captcha" in id_attr:
                            return True
            except:
                pass
        
        # Check page text for common verification keywords (only in visible body text, not scripts)
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            captcha_phrases = [
                "select 2 objects that are the same shape",
                "select 2 objects that have the same shape",
                "verify to continue",
                "drag the puzzle",
                "drag the slider",
                "verify you are human",
                "please solve the verification"
            ]
            if any(phrase in body_text for phrase in captcha_phrases):
                return True
        except:
            pass
            
    except:
        pass
    return False

def scrape_tiktok_live(driver, channel_profile_urls, topic_keywords):
    """Scrapes TikTok Live view count by checking live page directly. Returns (count, title, resolved_url)."""
    if isinstance(channel_profile_urls, str):
        channel_profile_urls = [channel_profile_urls]
        
    for url in channel_profile_urls:
        clean_url = url.split('?')[0].rstrip('/')
        if not clean_url.endswith("/live"):
            live_url = f"{clean_url}/live"
        else:
            live_url = clean_url
            
        profile_base_url = clean_url[:-5] if clean_url.endswith("/live") else clean_url
        handle = profile_base_url.split("@")[-1] if "@" in profile_base_url else "channel"
            
        print(f"  [TikTok] Loading live page directly: {live_url}")
        
        view_val = 0
        is_live = False
        
        # Try up to 2 attempts to load the live page and verify status
        for attempt in range(1, 3):
            if attempt > 1:
                print(f"  [TikTok] Retrying live page load (attempt {attempt}/2)...")
            try:
                try:
                    driver.set_page_load_timeout(15)
                except:
                    pass
                try:
                    driver.get(live_url)
                except Exception as get_err:
                    print(f"  [TikTok] Live page load timed out or hit error on attempt {attempt}: {get_err}")
                finally:
                    try:
                        driver.set_page_load_timeout(20)
                    except:
                        pass
                time.sleep(8)
                
                if check_is_tiktok_captcha(driver):
                    print("  [TikTok] WARNING: TikTok Captcha/Verification block detected! Page checks are blocked.")
                    break
                
                # Check offline indicators
                try:
                    body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
                    offline_indicators = [
                        "live has ended",
                        "suggested live streams",
                        "was live",
                        "is offline",
                        "การถ่ายทอดสดสิ้นสุดลงแล้ว"
                    ]
                    if any(ind in body_text for ind in offline_indicators):
                        print(f"  [TikTok] Channel is offline (found recommended/offline indicators).")
                        is_live = False
                        break
                except Exception as body_err:
                    print(f"  [TikTok] Error checking offline indicators on attempt {attempt}: {body_err}")
                
                # Find viewer counts in DOM
                xpath_query = "//*[self::span or self::div or self::p]"
                elements = driver.find_elements(By.XPATH, xpath_query)
                found_viewers = 0
                for el in elements:
                    try:
                        t = el.text.strip()
                        if t and len(t) < 50:
                            t_lower = t.lower()
                            if any(k in t_lower for k in ['watching', 'viewers', 'คนดู', 'ผู้ชม']):
                                val = clean_and_parse_count(t)
                                if val > 0:
                                    print(f"  [TikTok] Found viewers in DOM on attempt {attempt}: {val} (text: '{t}')")
                                    found_viewers = val
                                    break
                    except:
                        pass
                
                if found_viewers > 0:
                    view_val = found_viewers
                    is_live = True
                    break
            except Exception as e:
                print(f"  [TikTok] Error checking live page on attempt {attempt}: {e}")
                
        if is_live:
            print(f"  [TikTok] User is LIVE!")
            # Capture live screenshot
            screenshots_dir = os.path.join(EXTERNAL_DIR, "screenshots")
            if not os.path.exists(screenshots_dir):
                try:
                    os.makedirs(screenshots_dir)
                except Exception as dir_err:
                    print(f"  [TikTok] Error creating screenshots directory: {dir_err}")
                    
            now_dt = get_ict_now()
            timestamp_str = now_dt.strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"tiktok_{handle}_{timestamp_str}.png"
            filepath = os.path.join(screenshots_dir, filename)
            
            print(f"  [TikTok] Capturing live screenshot: {filepath}")
            try:
                driver.save_screenshot(filepath)
            except Exception as ss_err:
                print(f"  [TikTok] Failed to save screenshot locally: {ss_err}")
                filepath = None
                
            # Upload to Google Drive if configured
            drive_link = None
            if filepath and TOKEN_PATH and os.path.exists(TOKEN_PATH):
                print(f"  [TikTok] Uploading screenshot to Google Drive: {filename}")
                try:
                    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
                    drive_link = upload_screenshot_to_google_drive(creds, filepath, filename)
                except Exception as upload_err:
                    print(f"  [TikTok] Google Drive upload error: {upload_err}")
                    
            title_suffix = ""
            if drive_link:
                title_suffix = f" (Screenshot: {drive_link})"
            elif filepath:
                title_suffix = f" (Local Screenshot: {filename})"
                
            live_title = f"TikTok Live{title_suffix}"
            return view_val, live_title, live_url
        else:
            print(f"  [TikTok] User is currently NOT live on {live_url}.")
            
    return 0, "", ""

def scrape_x_live(driver, channel_profile_urls, topic_keywords):
    """Scrapes X (Twitter) Live view count by checking profile tweets. Returns (count, title)."""
    if isinstance(channel_profile_urls, str):
        channel_profile_urls = [channel_profile_urls]
        
    for url in channel_profile_urls:
        clean_url = url.split('?')[0].rstrip('/')
        target_title = ""
        try:
            target_url = None
            tweet_links = []
            broadcast_links = []
            if "/broadcasts/" in clean_url or "/status/" in clean_url:
                target_url = clean_url
                target_title = "X Broadcast" if "/broadcasts/" in clean_url else "X Status"
                print(f"  [X] Loading direct target URL: {target_url}")
            else:
                print(f"  [X] Loading profile page: {clean_url}")
                driver.get(clean_url)
                time.sleep(7)
                
                # Look for status links and broadcast links
                a_tags = driver.find_elements(By.TAG_NAME, "a")
                
                for a in a_tags:
                    try:
                        href = a.get_attribute("href")
                        if href:
                            if "/status/" in href:
                                tweet_links.append(href.split('?')[0])
                            elif "/broadcasts/" in href:
                                broadcast_links.append(href.split('?')[0])
                    except:
                        pass
                        
                tweet_links = list(set(tweet_links))
                broadcast_links = list(set(broadcast_links))
                
                print(f"  [X] Found {len(tweet_links)} tweets and {len(broadcast_links)} broadcasts on profile {clean_url}.")
            if broadcast_links:
                target_url = broadcast_links[0]
                target_title = "X Broadcast"
                print(f"  [X] Found active broadcast link on profile: {target_url}")
                
            if not target_url and tweet_links:
                # Let's inspect the text of the tweets on the page to match topic
                tweets = driver.find_elements(By.XPATH, "//article")
                for tweet in tweets:
                    try:
                        text = tweet.text
                        if any(k.lower() in text.lower() for k in topic_keywords):
                            # Find the status link inside this article
                            anchors = tweet.find_elements(By.TAG_NAME, "a")
                            for a in anchors:
                                href = a.get_attribute("href")
                                if href and "/status/" in href:
                                    target_url = href.split('?')[0]
                                    target_title = text.split('\n')[0] if text else "Matched Tweet"
                                    print(f"  [X] Matched topic in tweet text. Target tweet: {target_url}")
                                    break
                        if target_url:
                            break
                    except:
                        pass
                        
            if not target_url and tweet_links:
                # We no longer fall back to the first tweet status.
                pass
                
            if target_url:
                print(f"  [X] Loading page: {target_url}")
                driver.get(target_url)
                time.sleep(5)
                
                # Check if the stream is active live by looking for the LIVE/สด badge inside the video player
                is_active_live = False
                try:
                    players = driver.find_elements(By.XPATH, "//*[@data-testid='videoPlayer' or @data-testid='videoComponent']")
                    for player in players:
                        p_text = player.text
                        if "LIVE" in p_text.upper() or "สด" in p_text:
                            is_active_live = True
                            break
                    if not is_active_live:
                        badges = driver.find_elements(By.XPATH, "//*[text()='LIVE' or text()='Live' or text()='สด']")
                        for badge in badges:
                            if badge.is_displayed():
                                is_active_live = True
                                break
                except Exception as check_err:
                    print(f"  [X] Error checking active live status: {check_err}")
                    
                if not is_active_live:
                    print(f"  [X] Stream is not currently live (ended live stream or normal video): {target_url}")
                    return 0, target_title, target_url
                
                xpath_query = "//*[contains(text(), 'watching') or contains(text(), 'Watching') or contains(text(), 'view') or contains(text(), 'View') or contains(text(), 'คนดู') or contains(text(), 'ผู้ชม')]"
                elements = driver.find_elements(By.XPATH, xpath_query)
                for el in elements:
                    try:
                        t = el.text.strip()
                        if t and ("watching" in t.lower() or "view" in t.lower() or "คนดู" in t or "ผู้ชม" in t):
                            val = clean_and_parse_count(t)
                            if val > 0:
                                print(f"  [X] Found viewer count in DOM: {val} (text: '{t}')")
                                return val, target_title, target_url
                    except:
                        pass
                        
        except Exception as e:
            print(f"  [X] Error scraping profile {clean_url}: {e}")
            
    return 0, "", ""

# --- DATA STORAGE & ORCHESTRATION ---

def save_results_and_sheet(session_data, now_str, output_file):
    """Auxiliary helper to format, write, and dump scraped view count results to CSV and Google Sheets."""
    # Share Facebook live details of Thairath (Thairath online) with Thairath TV, PPTV, and Thai PBS
    if "Thairath" in session_data:
        thairath_fb_data = session_data["Thairath"].get("facebook", [])
        for target_pub in ["Thairath TV", "PPTV", "Thai PBS"]:
            if target_pub in session_data:
                session_data[target_pub]["facebook"] = thairath_fb_data
                
    # Construct result rows
    results_rows = []
    print("\n===== COMBINED SCRAPING RESULTS =====")
    for publisher_name, platforms_data in session_data.items():
        for platform, items in platforms_data.items():
            # Map platform name to proper display casing
            display_platform = {
                "youtube": "YouTube",
                "facebook": "Facebook",
                "tiktok": "TikTok",
                "x": "X"
            }.get(platform.lower(), platform)

            for item in items:
                if len(item) == 4:
                    count, title, url, stream_type = item
                else:
                    count, title, url = item
                    stream_type = "online"
                    
                # Map stream type to proper display casing
                display_type = {
                    "online": "Online",
                    "tv": "TV"
                }.get(stream_type.lower(), stream_type)

                if title and url:
                    formatted_title = f"{title} ({url})"
                elif title:
                    formatted_title = title
                else:
                    formatted_title = url or ""
                    
                row = {
                    "Time": now_str,
                    "Channel name": publisher_name,
                    "platform": display_platform,
                    "Type": display_type,
                    "views": count,
                    "title": formatted_title
                }
                results_rows.append(row)
                print(f"Result for {publisher_name} ({display_platform}): type={display_type}, views={count}, Title='{formatted_title}'")
                
    # Write to CSV
    if results_rows:
        file_exists = os.path.exists(output_file)
        with open(output_file, "a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["Time", "Channel name", "platform", "Type", "views", "title"])
            if not file_exists:
                writer.writeheader()
            for r in results_rows:
                writer.writerow(r)
        print(f"Results appended to {output_file}")
        
        # Write to Google Sheets
        write_to_google_sheet(results_rows)

def scrape_all_channels(topic_keywords, output_file=CSV_FILE, platforms_to_scrape=None, channels_to_scrape=None, targets_to_scrape=None):
    """Scrapes all channels and platforms concurrently in Phase 1, then scrapes active stream comments concurrently in Phase 2."""
    global CHANNELS, SPREADSHEET_ID, TOKEN_PATH, SCRAPE_COMMENTS, COMMENT_DURATION, MAX_CHECKING_WORKERS, MAX_COMMENT_WORKERS
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config_data = json.load(f)
                CHANNELS = config_data.get("channels", {})
                SPREADSHEET_ID = config_data.get("google_sheet_id", "")
                token_val = config_data.get("token_path", "")
                if token_val and not os.path.isabs(token_val):
                    TOKEN_PATH = os.path.join(EXTERNAL_DIR, token_val)
                else:
                    TOKEN_PATH = token_val
                SCRAPE_COMMENTS = config_data.get("scrape_comments", True)
                COMMENT_DURATION = config_data.get("comment_scrape_duration_seconds", 30)
                MAX_CHECKING_WORKERS = config_data.get("max_checking_workers", 6)
                MAX_COMMENT_WORKERS = config_data.get("max_comment_workers", 5)
    except Exception as e:
        print(f"Error reloading config.json at startup of run: {e}")

    now = get_ict_now()
    now_str = f"{now.day}/{now.month}/{now.year}, {now.strftime('%H:%M:%S')}"
    print(f"\n--- Scraping session started at {now_str} (ICT) ---")

    # Resolve target channels and platforms based on targets_to_scrape or fallback
    target_channels = CHANNELS
    if targets_to_scrape is not None:
        target_channels = {}
        for ch_name, platforms in CHANNELS.items():
            matched_key = None
            for k in targets_to_scrape:
                if k.lower() == ch_name.lower():
                    matched_key = k
                    break
            
            if matched_key:
                selected_platforms = targets_to_scrape[matched_key]
                filtered_platforms = {p: urls for p, urls in platforms.items() if p.lower() in [sp.lower() for sp in selected_platforms] or p == "default_urls"}
                target_channels[ch_name] = filtered_platforms
    else:
        if channels_to_scrape is not None:
            target_channels = {k: v for k, v in CHANNELS.items() if k.lower() in [c.lower() for c in channels_to_scrape]}
            
    # Initialize nested results structure for each publisher
    session_data = {}
    for pub_name in target_channels:
        session_data[pub_name] = {
            "youtube": [],
            "facebook": [],
            "tiktok": [],
            "x": []
        }
        
    if platforms_to_scrape is None:
        platforms_order = ["facebook", "youtube", "tiktok", "x"]
    else:
        platforms_order = [p.lower() for p in platforms_to_scrape if p.lower() in ["facebook", "youtube", "tiktok", "x"]]
    
    # Construct the flat list of view checking tasks
    tasks = []
    for platform in platforms_order:
        for publisher_name, platforms in target_channels.items():
            if platform in platforms:
                items = platforms[platform]
                if not items:
                    continue
                if isinstance(items, str):
                    items = [items]
                elif isinstance(items, dict):
                    items = [items]
                for item in items:
                    if isinstance(item, dict):
                        url = item.get("url", "")
                        stream_type = item.get("type", "online")
                    else:
                        url = item
                        stream_type = "online"
                    tasks.append((publisher_name, platform, url, stream_type))

    results = []
    comment_threads = []
    old_handler = None
    
    try:
        # Register SIGTERM to raise KeyboardInterrupt so we can gracefully save progress
        def sigterm_handler(signum, frame):
            print("\n[Signal] SIGTERM received. Raising KeyboardInterrupt for graceful stop...")
            raise KeyboardInterrupt
            
        old_handler = signal.signal(signal.SIGTERM, sigterm_handler)

        if tasks:
            print(f"\n===== PHASE 1: CONCURRENT VIEW CHECKING ({len(tasks)} links) =====")
            # Run the view check tasks concurrently
            checking_workers = MAX_CHECKING_WORKERS if MAX_CHECKING_WORKERS > 0 else len(tasks)
            checking_workers = max(1, checking_workers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=checking_workers) as executor:
                futures = {
                    executor.submit(
                        check_view_count_task, 
                        pub_name, platform, url, stream_type, topic_keywords
                    ): (pub_name, platform, url, stream_type)
                    for pub_name, platform, url, stream_type in tasks
                }
                for future in concurrent.futures.as_completed(futures):
                    try:
                        res = future.result()
                        results.append(res)
                    except Exception as exc:
                        pub_name, platform, url, stream_type = futures[future]
                        print(f"  Task generated an exception for {pub_name} ({platform.upper()}): {exc}")
                        results.append((pub_name, platform, url, 0, "", url, stream_type))
                        
            print("View checking completed.")
            
            # Map results to session data structure
            for pub_name, platform, url, count, title, resolved_url, stream_type in results:
                session_data[pub_name][platform].append((count, title, resolved_url, stream_type))
        else:
            print("No scraping tasks configured.")

        # Normal completion save
        save_results_and_sheet(session_data, now_str, output_file)

        # Phase 2: Parallel Background Comment Scraping
        if SCRAPE_COMMENTS:
            active_streams = [r for r in results if r[3] > 0]
            if active_streams:
                print(f"\n===== PHASE 2: PARALLEL COMMENTS SCRAPING ({len(active_streams)} streams) =====")
                comment_workers = MAX_COMMENT_WORKERS if MAX_COMMENT_WORKERS > 0 else len(active_streams)
                comment_workers = max(1, comment_workers)
                print(f"  [Comment Scraper] Running in parallel with a concurrency limit of {comment_workers} workers...")
                with concurrent.futures.ThreadPoolExecutor(max_workers=comment_workers) as executor:
                    futures = {
                        executor.submit(
                            scrape_comments_standalone,
                            pub_name, platform, resolved_url, title
                        ): (pub_name, platform, resolved_url)
                        for pub_name, platform, url, count, title, resolved_url, stream_type in active_streams
                    }
                    print(f"Waiting for parallel comment scrapers to finish ({COMMENT_DURATION} seconds)...")
                    concurrent.futures.wait(futures)
                print("All parallel comment scraping tasks completed.")
            else:
                print("\nNo active streams detected. Skipping comments scraping.")
        else:
            print("\nLive comment scraping is disabled. Skipping comments scraping.")
        
    except KeyboardInterrupt:
        print("\n[Graceful Stop] Scraper run interrupted. Saving partial results...")
        # Populate session data with whatever results we managed to gather
        for pub_name, platform, url, count, title, resolved_url, stream_type in results:
            already_added = False
            for existing in session_data[pub_name][platform]:
                if existing[2] == resolved_url:
                    already_added = True
                    break
            if not already_added:
                session_data[pub_name][platform].append((count, title, resolved_url, stream_type))
        save_results_and_sheet(session_data, now_str, output_file)
    except Exception as e:
        print(f"Error during scraping run: {e}")
    finally:
        # Restore default handler
        if old_handler is not None:
            try:
                signal.signal(signal.SIGTERM, old_handler)
            except:
                try:
                    signal.signal(signal.SIGTERM, signal.SIG_DFL)
                except:
                    pass

# --- SCHEDULER & LOOP MODE ---

def run_loop_scheduler(topic_keywords, platforms_to_scrape=None, channels_to_scrape=None, targets_to_scrape=None):
    """Runs a daemon loop that executes the scraping task at target intervals or target event times."""
    print("Starting LIVE capture scheduler loop...")
    
    next_scheduled_run_time = 0.0
    current_scheduler_interval = 0
    
    while True:
        # Reload configuration dynamically to check scheduler settings
        enabled = False
        interval_seconds = 0
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    scheduler_config = config_data.get("scheduler", {})
                    enabled = scheduler_config.get("enabled", False)
                    if enabled:
                        h = int(scheduler_config.get("hours", 0))
                        m = int(scheduler_config.get("minutes", 10))
                        s = int(scheduler_config.get("seconds", 0))
                        interval_seconds = h * 3600 + m * 60 + s
        except Exception as e:
            print(f"Error reloading config in scheduler loop: {e}")
            
        now_time = time.time()
        
        if enabled and interval_seconds > 0:
            # If interval changed or not initialized yet, reset target grid
            if current_scheduler_interval != interval_seconds or next_scheduled_run_time == 0.0:
                current_scheduler_interval = interval_seconds
                next_scheduled_run_time = now_time
                
            if now_time >= next_scheduled_run_time:
                # Trigger scraping
                print(f"Interval scheduler triggered for scheduled slot {next_scheduled_run_time}. Initiating scraping...")
                next_scheduled_run_time += interval_seconds
                try:
                    scrape_all_channels(
                        topic_keywords,
                        platforms_to_scrape=platforms_to_scrape,
                        channels_to_scrape=channels_to_scrape,
                        targets_to_scrape=targets_to_scrape
                    )
                except Exception as e:
                    print(f"Error during scheduled scraping run: {e}")
        else:
            # Fallback to the original hardcoded time-of-day scheduler
            # Generate target trigger times: every 10 minutes from 16:20 to 19:10
            TARGET_TIMES = []
            for m in range(20, 60, 10):
                TARGET_TIMES.append((16, m))
            for h in [17, 18]:
                for m in range(0, 60, 10):
                    TARGET_TIMES.append((h, m))
            for m in range(0, 20, 10):
                TARGET_TIMES.append((19, m))
                
            now_dt = get_ict_now()
            curr_time = (now_dt.hour, now_dt.minute)
            trigger_key = (now_dt.day, now_dt.hour, now_dt.minute)
            
            if not hasattr(run_loop_scheduler, 'last_time_trigger_key'):
                run_loop_scheduler.last_time_trigger_key = None
                
            if curr_time in TARGET_TIMES and run_loop_scheduler.last_time_trigger_key != trigger_key:
                print(f"Trigger time detected: {now_dt.strftime('%H:%M:%S')}. Initiating scraping...")
                run_loop_scheduler.last_time_trigger_key = trigger_key
                try:
                    scrape_all_channels(
                        topic_keywords,
                        platforms_to_scrape=platforms_to_scrape,
                        channels_to_scrape=channels_to_scrape,
                        targets_to_scrape=targets_to_scrape
                    )
                except Exception as e:
                    print(f"Error during scheduled scraping run: {e}")
                    
        # Sleep for 5 seconds before checking again
        time.sleep(5)

# --- INTERACTIVE KEYWORD SCANNING HELPERS ---

def scan_youtube_for_keyword(driver, default_url, keyword):
    """Scans YouTube streams page for live streams matching the keyword."""
    clean_url = default_url.split('?')[0].rstrip('/')
    if not clean_url.endswith("/streams"):
        clean_url = clean_url + "/streams"
        
    print(f"  [Scan YouTube] Loading streams page: {clean_url}")
    matches = []
    try:
        driver.get(clean_url)
        time.sleep(5)
        
        a_tags = driver.find_elements(By.TAG_NAME, "a")
        watch_links = {}
        for a in a_tags:
            try:
                href = a.get_attribute("href")
                text = a.text.strip()
                if href and "/watch?v=" in href:
                    clean_href = href.split('&')[0]
                    if clean_href not in watch_links:
                        watch_links[clean_href] = []
                    if text:
                        watch_links[clean_href].append(text)
            except:
                pass
                
        for watch_url, texts in watch_links.items():
            is_live = False
            is_upcoming = False
            title = ""
            viewers_str = ""
            for t in texts:
                t_clean = t.strip()
                t_clean_upper = t_clean.upper()
                if t_clean_upper in ["LIVE", "🔴LIVE", "🔴 LIVE"] or t_clean in ["สด", "🔴สด", "🔴 สด"]:
                    is_live = True
                elif t_clean_upper in ["UPCOMING", "🔴UPCOMING", "🔴 UPCOMING"] or "กำลังจะมาถึง" in t_clean or "เริ่มแพร่ภาพสดในอีก" in t_clean or "กำหนดเวลาสำหรับ" in t_clean or "รอการออกอากาศ" in t_clean or "scheduled" in t_clean.lower():
                    is_upcoming = True
                else:
                    if "watching" in t.lower() or "ผู้ชม" in t or "คนดู" in t:
                        viewers_str = t
                    elif len(t) > len(title):
                        title = t
            
            if is_live or is_upcoming:
                if keyword.lower() in title.lower():
                    viewers = clean_and_parse_count(viewers_str) if (is_live and viewers_str) else 0
                    display_title = f"[Upcoming] {title}" if is_upcoming else title
                    matches.append({
                        "url": watch_url,
                        "title": display_title,
                        "viewers": viewers
                    })
    except Exception as e:
        print(f"  [Scan YouTube] Error scanning default URL: {e}")
        
    return matches

def scan_facebook_for_keyword(driver, default_url, keyword):
    """Scans Facebook page feed posts (up to 20) for active candidate videos matching the keyword."""
    print(f"  [Scan Facebook] Loading page: {default_url}")
    matches = []
    
    resolved_page_id = None
    
    # Extract unique identifier from default_url
    url_lower = default_url.lower()
    page_id_match = re.search(r'id=(\d+)', url_lower)
    if page_id_match:
        page_identifier = page_id_match.group(1)
    else:
        page_identifier = url_lower.split('?')[0].rstrip('/').split('/')[-1]
    print(f"  [Scan Facebook] Page identifier resolved for feed validation: {page_identifier}")
    
    # Automatically convert to the optimal watch format URL if not already in that format
    if "facebook.com/watch/" not in default_url.lower():
        if page_id_match:
            if "profile.php" in default_url.lower():
                default_url = f"https://www.facebook.com/watch/profile.php?id={page_identifier}"
            else:
                default_url = f"https://www.facebook.com/watch/{page_identifier}/"
        else:
            default_url = f"https://www.facebook.com/watch/{page_identifier}/"
        print(f"  [Scan Facebook] Automatically optimized scan URL to watch layout: {default_url}")
        
    try:
        driver.get(default_url)
        time.sleep(5)
        
        # Resolve page numeric ID from page source for link validation
        try:
            src = driver.page_source
            container_match = re.search(r'"container_id"\s*:\s*"(\d+)"', src)
            if container_match:
                resolved_page_id = container_match.group(1)
                print(f"  [Scan Facebook] Resolved numeric page ID from container_id: {resolved_page_id}")
            else:
                owner_match = re.search(r'"owner"\s*:\s*\{\s*"__typename"\s*:\s*"User"\s*,\s*"id"\s*:\s*"(\d+)"', src)
                if owner_match:
                    resolved_page_id = owner_match.group(1)
                    print(f"  [Scan Facebook] Resolved numeric page ID from owner ID: {resolved_page_id}")
        except Exception as e:
            print(f"  [Scan Facebook] Warning: could not resolve numeric page ID: {e}")
        
        # 1. Look for active profile live badge anchor first
        print("  [Scan Facebook] Searching for active profile live badge...")
        live_anchors = driver.find_elements(By.XPATH, "//a[contains(@aria-label, 'is on Live') or contains(@aria-label, 'is live') or contains(@aria-label, 'กำลังรับชม') or contains(@aria-label, 'สด')]")
        profile_live_url = None
        matched_aria_label = ""
        for anchor in live_anchors:
            try:
                href = anchor.get_attribute("href")
                if href:
                    clean_href = href.split('?')[0].split('#')[0]
                    video_id_match = re.search(r'/videos/(?:[^/]+/)*(\d+)', clean_href) or re.search(r'[?&]v=(\d+)', href) or re.search(r'/live/(?:[^/]+/)*(\d+)', clean_href)
                    if video_id_match:
                        video_id = video_id_match.group(1)
                        profile_live_url = f"https://www.facebook.com/watch/?v={video_id}"
                        matched_aria_label = anchor.get_attribute("aria-label") or ""
                        break
            except:
                pass

        if profile_live_url:
            embed_url = f"https://www.facebook.com/plugins/video.php?href={profile_live_url}&show_text=true"
            print(f"  [Scan Facebook] Active live badge detected! Loading plugins embed URL: {embed_url}")
            driver.get(embed_url)
            time.sleep(5)
            
            # Dismiss the login dialog if one displays inside the iframe
            try:
                dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                if dialogs:
                    for xpath in ["//div[@role='dialog']//div[@aria-label='Close']", "//div[@role='dialog']//div[@aria-label='ปิด']", "//div[@aria-label='Close']", "//div[@aria-label='ปิด']"]:
                        try:
                            close_btn = driver.find_element(By.XPATH, xpath)
                            if close_btn.is_displayed():
                                close_btn.click()
                                time.sleep(1)
                                break
                        except:
                            pass
            except:
                pass
                
            # Verify if active live indicators are present in the embed player
            is_live_video = False
            live_indicators = driver.find_elements(By.CSS_SELECTOR, "._u_g, ._u_h")
            for el in live_indicators:
                if el.is_displayed():
                    is_live_video = True
                    break
                    
            if is_live_video:
                title = ""
                try:
                    title_elements = driver.find_elements(By.CSS_SELECTOR, "._4i-s, ._5pbx, h1, h2")
                    for el in title_elements:
                        t = el.text.strip()
                        if t:
                            title = t
                            break
                except:
                    pass
                    
                if not title:
                    title = driver.title or "Facebook Live"
                if title.endswith(" | Facebook"):
                    title = title[:-11]
                elif title.endswith(" - Facebook"):
                    title = title[:-11]
                    
                is_matched = (keyword.lower() in title.lower())
                if not is_matched and matched_aria_label:
                    if keyword.lower() in ["สด", "live", "live now"] and any(w in matched_aria_label.lower() for w in ["live", "สด"]):
                        is_matched = True
                        
                if is_matched:
                    view_val = 0
                    try:
                        viewer_elements = driver.find_elements(By.CSS_SELECTOR, "._u_l")
                        for el in viewer_elements:
                            if el.is_displayed():
                                t = el.text.strip()
                                if t:
                                    v = clean_and_parse_count(t)
                                    if v > 0:
                                        view_val = v
                                        break
                    except:
                        pass
                        
                    if view_val == 0:
                        try:
                            body_text = driver.find_element(By.TAG_NAME, "body").text
                            match = re.search(r'(?:LIVE|สด)\s*([\d.,KkMmพันล้าน]+)', body_text)
                            if match:
                                v = clean_and_parse_count(match.group(1))
                                if v > 0:
                                    view_val = v
                        except:
                            pass
                            
                    matches.append({
                        "url": profile_live_url,
                        "title": title,
                        "viewers": view_val
                    })
                    print(f"  [Scan Facebook] Match successfully added from live badge: {title} ({view_val} viewers)")
                    return matches

        # Fallback to feed posts scanning if no badge matched
        print("  [Scan Facebook] No active live badge matched. Scrolling feed...")
        # Scroll down to load posts (top 20 posts)
        for i in range(4):
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2.5)
            
        articles = driver.find_elements(By.XPATH, "//div[@role='article'] | //div[@role='listitem']")
        print(f"  [Scan Facebook] Found {len(articles)} posts in feed. Searching for candidates matching keyword...")
        
        seen_urls = set()
        
        for article in articles[:20]:
            try:
                # Find links inside this article
                a_tags = article.find_elements(By.TAG_NAME, "a")
                
                # Verify owner to filter out recommended/suggested posts
                is_owner = False
                for a in a_tags[:4]:
                    try:
                        href = a.get_attribute("href")
                        if href:
                            href_lower = href.lower()
                            if page_identifier and page_identifier.lower() in href_lower:
                                is_owner = True
                                break
                            if resolved_page_id and resolved_page_id in href_lower:
                                is_owner = True
                                break
                    except:
                        pass
                if not is_owner:
                    continue
                
                # Check if this post is an active LIVE video stream
                is_live_video = False
                try:
                    # Method 1: Check for the specific CSS class .x1ciooss
                    live_badges = article.find_elements(By.CLASS_NAME, "x1ciooss")
                    if live_badges:
                        is_live_video = True
                except:
                    pass
                    
                if not is_live_video:
                    try:
                        # Method 2: Check for exact text badge 'สด' or 'LIVE' or 'LIVE NOW'
                        badges = article.find_elements(By.XPATH, ".//*[text()='สด' or text()='LIVE' or text()='LIVE NOW']")
                        if badges:
                            is_live_video = True
                    except:
                        pass
                        
                if not is_live_video:
                    continue
                
                
                text = article.text.strip()
                video_url = None
                for a in a_tags:
                    try:
                        href = a.get_attribute("href")
                        if href and any(x in href for x in ["/videos/", "/watch/", "/live/"]):
                            # Robust extraction to avoid page ID match (handles slugs in path)
                            clean_href = href.split('?')[0].split('#')[0]
                            match = re.search(r'/videos/(?:[^/]+/)*(\d+)', clean_href) or re.search(r'[?&]v=(\d+)', href) or re.search(r'/live/(?:[^/]+/)*(\d+)', clean_href)
                            if match:
                                video_id = match.group(1)
                                clean_url = f"https://www.facebook.com/watch/?v={video_id}"
                                if clean_url not in seen_urls:
                                    video_url = clean_url
                                    break
                    except:
                        pass
                
                if video_url:
                    seen_urls.add(video_url)
                    
                    # Deduce a title from the post text
                    lines = [l.strip() for l in text.split('\n') if l.strip()]
                    title = "Facebook Video"
                    for line in lines:
                        if len(line) > 10 and not any(k in line.lower() for k in ["like", "comment", "share", "follow", "ชม", "คน", "แชร์", "ไลก์", "ถูกใจ"]):
                            title = line
                            break
                            
                    # Filter by keyword in text or title
                    is_matched = False
                    if keyword.lower() in text.lower() or keyword.lower() in title.lower():
                        is_matched = True
                    elif keyword.lower() in ["สด", "live", "live now"] and is_live_video:
                        is_matched = True
                        
                    if is_matched:
                        matches.append({
                            "url": video_url,
                            "title": title,
                            "viewers": 0
                        })
            except:
                pass
                
        # Fetch viewer count only for matching targets
        print(f"  [Scan Facebook] Found {len(matches)} matching targets. Fetching concurrent viewers...")
        for match in matches[:5]:
            try:
                embed_url = f"https://www.facebook.com/plugins/video.php?href={match['url']}&show_text=true"
                print(f"  [Scan Facebook] Loading embed for candidate: {embed_url}")
                driver.get(embed_url)
                time.sleep(5)
                
                # Dismiss the login dialog if one displays inside the iframe
                try:
                    dialogs = driver.find_elements(By.XPATH, "//div[@role='dialog']")
                    if dialogs:
                        for xpath in ["//div[@role='dialog']//div[@aria-label='Close']", "//div[@role='dialog']//div[@aria-label='ปิด']", "//div[@aria-label='Close']", "//div[@aria-label='ปิด']"]:
                            try:
                                close_btn = driver.find_element(By.XPATH, xpath)
                                if close_btn.is_displayed():
                                    close_btn.click()
                                    time.sleep(1)
                                    break
                            except:
                                pass
                except:
                    pass
                    
                # Verify if active live indicators are present in the embed player
                is_live_video = False
                live_indicators = driver.find_elements(By.CSS_SELECTOR, "._u_g, ._u_h")
                for el in live_indicators:
                    if el.is_displayed():
                        is_live_video = True
                        break
                        
                if not is_live_video:
                    print(f"  [Scan Facebook] Candidate {match['url']} is not live in embed. Skipping.")
                    match["viewers"] = 0
                    continue
                    
                # Fetch clean title if fallback was used
                if match["title"] == "Facebook Video":
                    try:
                        title_elements = driver.find_elements(By.CSS_SELECTOR, "._4i-s, ._5pbx, h1, h2")
                        for el in title_elements:
                            t = el.text.strip()
                            if t:
                                match["title"] = t
                                break
                    except:
                        pass
                        
                # Find concurrent viewers
                view_val = 0
                try:
                    viewer_elements = driver.find_elements(By.CSS_SELECTOR, "._u_l")
                    for el in viewer_elements:
                        if el.is_displayed():
                            t = el.text.strip()
                            if t:
                                v = clean_and_parse_count(t)
                                if v > 0:
                                    view_val = v
                                    break
                except:
                    pass
                    
                if view_val == 0:
                    try:
                        body_text = driver.find_element(By.TAG_NAME, "body").text
                        match_views = re.search(r'(?:LIVE|สด)\s*([\d.,KkMmพันล้าน]+)', body_text)
                        if match_views:
                            v = clean_and_parse_count(match_views.group(1))
                            if v > 0:
                                view_val = v
                    except:
                        pass
                        
                match["viewers"] = view_val
                print(f"  [Scan Facebook] Candidate {match['url']} has {view_val} viewers.")
            except Exception as e:
                print(f"  [Scan Facebook] Error fetching viewers for candidate {match['url']}: {e}")
                
    except Exception as e:
        print(f"  [Scan Facebook] Error scanning default URL: {e}")
        
    return matches

def scan_tiktok_for_keyword(driver, default_url, keyword):
    """Checks if TikTok is live, parses viewers if live, and checks keyword."""
    clean_url = default_url.split('?')[0].rstrip('/')
    if not clean_url.endswith("/live"):
        live_url = f"{clean_url}/live"
    else:
        live_url = clean_url
        
    print(f"  [Scan TikTok] Loading live page directly: {live_url}")
    matches = []
    try:
        driver.get(live_url)
        time.sleep(8)
        
        if check_is_tiktok_captcha(driver):
            print("  [Scan TikTok] WARNING: TikTok Captcha/Verification block detected! Scan is blocked.")
            return matches
            
        # Check offline indicators
        try:
            body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            offline_indicators = [
                "live has ended",
                "suggested live streams",
                "was live",
                "is offline",
                "การถ่ายทอดสดสิ้นสุดลงแล้ว"
            ]
            if any(ind in body_text for ind in offline_indicators):
                print(f"  [Scan TikTok] Channel is offline (found recommended/offline indicators).")
                return matches
        except:
            pass
            
        title = driver.title or "TikTok Live"
        view_val = 0
        
        xpath_query = "//*[self::span or self::div or self::p]"
        elements = driver.find_elements(By.XPATH, xpath_query)
        for el in elements:
            try:
                t = el.text.strip()
                if t and len(t) < 50:
                    t_lower = t.lower()
                    if any(k in t_lower for k in ['watching', 'viewers', 'คนดู', 'ผู้ชม']):
                        val = clean_and_parse_count(t)
                        if val > 0:
                            view_val = val
                            break
            except:
                pass
                
        if view_val > 0:
            if keyword.lower() in title.lower() or keyword == "":
                matches.append({
                    "url": live_url,
                    "title": title,
                    "viewers": view_val
                })
    except Exception as e:
        print(f"  [Scan TikTok] Error scanning default URL: {e}")
        
    return matches

def scan_x_for_keyword(driver, default_url, keyword):
    """Scans X profile feed for active broadcasts/tweets matching the keyword."""
    clean_url = default_url.split('?')[0].rstrip('/')
    matches = []
    try:
        target_url = None
        target_title = ""
        
        if "/broadcasts/" in clean_url or "/status/" in clean_url:
            target_url = clean_url
            target_title = "X Broadcast" if "/broadcasts/" in clean_url else "X Status"
            print(f"  [Scan X] Detected direct target URL: {target_url}")
        else:
            print(f"  [Scan X] Loading profile page: {clean_url}")
            driver.get(clean_url)
            time.sleep(7)
            
            a_tags = driver.find_elements(By.TAG_NAME, "a")
            tweet_links = []
            broadcast_links = []
            for a in a_tags:
                try:
                    href = a.get_attribute("href")
                    if href:
                        if "/status/" in href:
                            tweet_links.append(href.split('?')[0])
                        elif "/broadcasts/" in href:
                            broadcast_links.append(href.split('?')[0])
                except:
                    pass
                    
            tweet_links = list(set(tweet_links))
            broadcast_links = list(set(broadcast_links))
            
            if broadcast_links:
                target_url = broadcast_links[0]
                target_title = "X Broadcast"
                
            if not target_url and tweet_links:
                tweets = driver.find_elements(By.XPATH, "//article")
                for tweet in tweets:
                    try:
                        text = tweet.text
                        if keyword.lower() in text.lower():
                            anchors = tweet.find_elements(By.TAG_NAME, "a")
                            for a in anchors:
                                href = a.get_attribute("href")
                                if href and "/status/" in href:
                                    target_url = href.split('?')[0]
                                    target_title = text.split('\n')[0] if text else "Matched Tweet"
                                    break
                        if target_url:
                            break
                    except:
                        pass
                        
        if target_url:
            print(f"  [Scan X] Inspecting target link: {target_url}")
            driver.get(target_url)
            time.sleep(5)
            
            # Check if the stream is active live by looking for the LIVE/สด badge inside the video player
            is_active_live = False
            try:
                players = driver.find_elements(By.XPATH, "//*[@data-testid='videoPlayer' or @data-testid='videoComponent']")
                for player in players:
                    p_text = player.text
                    if "LIVE" in p_text.upper() or "สด" in p_text:
                        is_active_live = True
                        break
                if not is_active_live:
                    badges = driver.find_elements(By.XPATH, "//*[text()='LIVE' or text()='Live' or text()='สด']")
                    for badge in badges:
                        if badge.is_displayed():
                            is_active_live = True
                            break
            except Exception as check_err:
                print(f"  [Scan X] Error checking active live status: {check_err}")
                
            if is_active_live:
                view_val = 0
                xpath_query = "//*[contains(text(), 'watching') or contains(text(), 'Watching') or contains(text(), 'view') or contains(text(), 'View') or contains(text(), 'คนดู') or contains(text(), 'ผู้ชม')]"
                elements = driver.find_elements(By.XPATH, xpath_query)
                for el in elements:
                    try:
                        t = el.text.strip()
                        if t and ("watching" in t.lower() or "view" in t.lower() or "คนดู" in t or "ผู้ชม" in t):
                            val = clean_and_parse_count(t)
                            if val > 0:
                                view_val = val
                                break
                    except:
                        pass
                        
                matches.append({
                    "url": target_url,
                    "title": target_title,
                    "viewers": view_val
                })
            else:
                print(f"  [Scan X] Inspected link is not active live: {target_url}")
    except Exception as e:
        print(f"  [Scan X] Error scanning default URL: {e}")
        
    return matches

def main():
    parser = argparse.ArgumentParser(description="LIVE Stream View Count Scraper")
    parser.add_argument("--loop", action="store_true", help="Run in daemon scheduler mode (monitors 17:00 - 19:05 ICT at target times)")
    parser.add_argument("--test-run", action="store_true", help="Run once immediately for testing, saving to test_view_counts.csv & Google Sheets")
    parser.add_argument("--platforms", nargs="+", help="Platforms to scrape (youtube, facebook, tiktok, x)")
    parser.add_argument("--channels", nargs="+", help="Channels to scrape")
    parser.add_argument("--targets", help="JSON string mapping channels to lists of platforms to scrape")
    args = parser.parse_args()
    
    topic_keywords = ["Think Tank Bangkok", "Think Tank", "Bangkok"]
    
    platforms_to_scrape = None
    if args.platforms:
        platforms_to_scrape = [p.lower() for p in args.platforms]
        
    channels_to_scrape = None
    if args.channels:
        channels_to_scrape = args.channels
        
    targets_to_scrape = None
    if args.targets:
        try:
            targets_to_scrape = json.loads(args.targets)
        except Exception as e:
            print(f"Error parsing targets JSON: {e}")
    
    if args.test_run:
        print("Executing immediate test run...")
        scrape_all_channels(topic_keywords, output_file="test_view_counts.csv", platforms_to_scrape=platforms_to_scrape, channels_to_scrape=channels_to_scrape, targets_to_scrape=targets_to_scrape)
        print("Test run complete. Output saved to test_view_counts.csv and updated on Google Sheets.")
    elif args.loop:
        run_loop_scheduler(
            topic_keywords,
            platforms_to_scrape=platforms_to_scrape,
            channels_to_scrape=channels_to_scrape,
            targets_to_scrape=targets_to_scrape
        )
    else:
        # Default single run mode
        scrape_all_channels(topic_keywords, platforms_to_scrape=platforms_to_scrape, channels_to_scrape=channels_to_scrape, targets_to_scrape=targets_to_scrape)

if __name__ == "__main__":
    main()
