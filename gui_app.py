import os
import sys
import io
if sys.platform == "win32":
    if sys.stdout is None:  # happens with --noconsole
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    else:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    else:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
        
import json
import time
import subprocess
import threading
import atexit
import signal
from http.server import BaseHTTPRequestHandler, HTTPServer
import selenium.webdriver.chrome.webdriver
import selenium.webdriver.chrome.options
import selenium.webdriver.chrome.service

PORT = 8000

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

DIRECTORY = sys._MEIPASS if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
EXTERNAL_DIR = get_external_dir()
CONFIG_PATH = os.path.join(EXTERNAL_DIR, "config.json")

# Global state
scraper_process = None
active_login_driver = None
scraper_logs = []
scraper_status = "idle"
status_lock = threading.Lock()
dashboard_browser_process = None
last_heartbeat_time = time.time()

# Register clean-up on exit
def cleanup_gui_app():
    global scraper_process, active_login_driver, dashboard_browser_process
    print("[Cleanup] Stopping running GUI app resources...")
    
    # 0. Terminate the dashboard browser process if it's running
    if dashboard_browser_process:
        try:
            print("[Cleanup] Terminating dashboard Chrome process...")
            dashboard_browser_process.terminate()
            dashboard_browser_process.wait(timeout=2)
            print("[Cleanup] Dashboard Chrome process terminated successfully.")
        except Exception as e:
            try:
                dashboard_browser_process.kill()
                print("[Cleanup] Dashboard Chrome process killed forcefully.")
            except:
                pass
    
    # 1. Terminate the scraper process if it's running
    if scraper_process:
        try:
            print(f"[Cleanup] Terminating scraper process {scraper_process.pid}...")
            scraper_process.terminate()
            scraper_process.wait(timeout=2)
            print("[Cleanup] Scraper process terminated successfully.")
        except Exception as e:
            try:
                scraper_process.kill()
                print("[Cleanup] Scraper process killed forcefully.")
            except:
                pass
                
    # 2. Quit any active login helper driver
    if active_login_driver:
        try:
            print("[Cleanup] Quitting active login helper driver...")
            active_login_driver.quit()
            print("[Cleanup] Active login helper driver closed.")
        except Exception as e:
            pass

atexit.register(cleanup_gui_app)

def handle_sigterm(signum, frame):
    print(f"\n[Signal] Received signal {signum}. Exiting cleanly...")
    sys.exit(0)

# Register signal handlers for graceful exit
signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

logs_lock = threading.Lock()
next_scheduled_run_time = None
current_scheduler_interval = None
scheduler_active = False
scheduler_targets = None

import urllib.request
import re

# Background Link Status Checker Cache
link_statuses = {}  # { url: { "status": "live"/"offline"/"checking", "viewers": int, "timestamp": float } }
status_cache_lock = threading.Lock()
refresh_status_event = threading.Event()

def check_tiktok_status(driver, url):
    try:
        import bs4
        from selenium.webdriver.common.by import By
        from live_scraper import clean_and_parse_count, check_is_tiktok_captcha
        
        # Ensure url ends with /live
        clean_url = url.split('?')[0].rstrip('/')
        if not clean_url.endswith("/live"):
            live_url = f"{clean_url}/live"
        else:
            live_url = clean_url
            
        print(f"[Background Checker] Checking TikTok status for {live_url} via Selenium...")
        try:
            driver.set_page_load_timeout(15)
        except:
            pass
            
        try:
            driver.get(live_url)
        except Exception as get_err:
            print(f"[Background Checker] Error loading TikTok live URL: {get_err}")
        finally:
            try:
                driver.set_page_load_timeout(20)
            except:
                pass
                
        # Wait and poll for status
        start_time = time.time()
        status, view_val = "offline", 0
        
        while time.time() - start_time < 12:
            if check_is_tiktok_captcha(driver):
                print(f"[Background Checker] WARNING: TikTok Captcha/Verification block detected for {live_url}!")
                return "offline", 0
                
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
                    status = "offline"
                    view_val = 0
                    break
            except:
                pass
                
            # Try to find viewer count
            found_viewers = 0
            try:
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
                                    found_viewers = val
                                    break
                    except:
                        pass
            except:
                pass
                
            if found_viewers > 0:
                status = "live"
                view_val = found_viewers
                break
                
            time.sleep(1.5)
            
        return status, view_val
    except Exception as e:
        print(f"[Background Checker] Error checking TikTok status for {url}: {e}")
        return "offline", 0

def check_youtube_status(driver, url):
    try:
        from live_scraper import clean_and_parse_count, check_is_youtube_upcoming, check_is_youtube_active_live
        from selenium.webdriver.common.by import By
        
        if "/watch" in url or "/live/" in url or "youtu.be/" in url:
            driver.get(url)
            time.sleep(4)
            if check_is_youtube_active_live(driver):
                view_val = 0
                try:
                    xpath_query = "//*[contains(text(), 'watching') or contains(text(), 'ผู้ชม') or contains(text(), 'คนดู')]"
                    elements = driver.find_elements(By.XPATH, xpath_query)
                    for el in elements:
                        t = el.text.strip()
                        if t and ("watching" in t.lower() or "ผู้ชม" in t or "คนดู" in t):
                            if "views" not in t.lower() and "การดู" not in t:
                                v = clean_and_parse_count(t)
                                if v > 0:
                                    view_val = v
                                    break
                except:
                    pass
                return "live", view_val
                
            if check_is_youtube_upcoming(driver):
                return "offline", 0
            return "offline", 0
        else:
            # Channel page
            clean_url = url.split('?')[0].rstrip('/')
            if not clean_url.endswith("/streams"):
                clean_url = clean_url + "/streams"
            driver.get(clean_url)
            time.sleep(4)
            a_tags = driver.find_elements(By.TAG_NAME, "a")
            for a in a_tags:
                try:
                    text = a.text.strip()
                    if text.upper() in ["LIVE", "🔴LIVE", "🔴 LIVE"] or text in ["สด", "🔴สด", "🔴 สด"]:
                        return "live", 0
                except:
                    pass
            return "offline", 0
    except Exception as e:
        print(f"Error checking YouTube status for {url}: {e}")
        return "offline", 0

def check_facebook_status(driver, url):
    try:
        from live_scraper import clean_and_parse_count
        from selenium.webdriver.common.by import By
        import re
        
        if "/videos/" in url or "/watch/" in url or "/live/" in url or "v=" in url:
            # Direct video URL -> use plugins embed player to bypass login wall
            embed_url = f"https://www.facebook.com/plugins/video.php?href={url}&show_text=true"
            driver.get(embed_url)
            time.sleep(4)
            
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
                return "offline", 0
                
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
                    match = re.search(r'(?:LIVE|สด)\s*([\d.,KkMmพันล้าน]+)', body_text)
                    if match:
                        v = clean_and_parse_count(match.group(1))
                        if v > 0:
                            view_val = v
                except:
                    pass
                    
            return "live", view_val
        else:
            # Channel page
            driver.get(url)
            time.sleep(4)
            # Page watch list or feed
            live_anchors = driver.find_elements(By.XPATH, "//a[contains(@aria-label, 'is on Live') or contains(@aria-label, 'is live') or contains(@aria-label, 'กำลังรับชม') or contains(@aria-label, 'สด')]")
            if live_anchors:
                return "live", 0
            articles = driver.find_elements(By.XPATH, "//div[@role='article'] | //div[@role='listitem']")
            for article in articles[:5]:
                try:
                    is_live = False
                    if article.find_elements(By.CLASS_NAME, "x1ciooss"):
                        is_live = True
                    elif article.find_elements(By.XPATH, ".//*[text()='สด' or text()='LIVE' or text()='LIVE NOW']"):
                        is_live = True
                    if is_live:
                        return "live", 0
                except:
                    pass
            return "offline", 0
    except Exception as e:
        print(f"Error checking Facebook status for {url}: {e}")
        return "offline", 0

def check_x_status(driver, url):
    try:
        from selenium.webdriver.common.by import By
        driver.get(url)
        time.sleep(4)
        if "/broadcasts/" in url:
            src = driver.page_source.lower()
            if any(k in src for k in ["broadcast ended", "ended", "สิ้นสุดแล้ว", "การถ่ายทอดสดสิ้นสุดลงแล้ว"]):
                return "offline", 0
            return "live", 0
        else:
            a_tags = driver.find_elements(By.TAG_NAME, "a")
            broadcast_url = None
            for a in a_tags:
                try:
                    href = a.get_attribute("href")
                    if href and "/broadcasts/" in href:
                        broadcast_url = href
                        break
                except:
                    pass
            
            if broadcast_url:
                driver.get(broadcast_url)
                time.sleep(4)
                src = driver.page_source.lower()
                if any(k in src for k in ["broadcast ended", "ended", "สิ้นสุดแล้ว", "การถ่ายทอดสดสิ้นสุดลงแล้ว"]):
                    return "offline", 0
                return "live", 0
            return "offline", 0
    except Exception as e:
        print(f"Error checking X status for {url}: {e}")
        return "offline", 0

def status_checker_worker():
    print("[Background Checker] Thread started.")
    time.sleep(10)
    
    while True:
        try:
            urls_to_check = []
            if os.path.exists(CONFIG_PATH):
                try:
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    for channel_name, platform_map in config_data.get("channels", {}).items():
                        for platform in ["youtube", "facebook", "tiktok", "x"]:
                            rows = platform_map.get(platform, [])
                            for row in rows:
                                url = row.get("url", "").strip() if isinstance(row, dict) else row.strip()
                                if url:
                                    urls_to_check.append((platform, url))
                except Exception as ce:
                    print(f"[Background Checker] Error loading config: {ce}")
            
            unique_urls = []
            seen = set()
            for platform, url in urls_to_check:
                if url not in seen:
                    seen.add(url)
                    unique_urls.append((platform, url))
            
            # Filter to find URLs that actually need verification (new/checking, or expired > 5 mins)
            urls_needing_check = []
            now = time.time()
            with status_cache_lock:
                for platform, url in unique_urls:
                    if url not in link_statuses:
                        link_statuses[url] = { "status": "checking", "viewers": 0, "timestamp": 0 }
                        urls_needing_check.append((platform, url))
                    else:
                        info = link_statuses[url]
                        if info.get("status") == "checking" or (now - info.get("timestamp", 0) >= 300):
                            urls_needing_check.append((platform, url))
            
            # If no URLs need verification, we wait/sleep
            if not urls_needing_check:
                refreshed = refresh_status_event.wait(timeout=15)
                if refreshed:
                    refresh_status_event.clear()
                continue
                
            print(f"[Background Checker] Checking {len(urls_needing_check)} links in this cycle...")
            
            if urls_needing_check:
                from live_scraper import setup_stealth_driver
                driver = None
                try:
                    driver = setup_stealth_driver()
                    for platform, url in urls_needing_check:
                        status, viewers = "offline", 0
                        if platform == "youtube":
                            status, viewers = check_youtube_status(driver, url)
                        elif platform == "facebook":
                            status, viewers = check_facebook_status(driver, url)
                        elif platform == "tiktok":
                            status, viewers = check_tiktok_status(driver, url)
                        elif platform == "x":
                            status, viewers = check_x_status(driver, url)
                            
                        with status_cache_lock:
                            link_statuses[url] = {
                                "status": status,
                                "viewers": viewers,
                                "timestamp": time.time()
                            }
                except Exception as se:
                    print(f"[Background Checker] Selenium check error: {se}")
                    # Reset checking status of remaining URLs to let them retry later
                    with status_cache_lock:
                        for platform, url in urls_needing_check:
                            if url in link_statuses and link_statuses[url].get("status") == "checking":
                                link_statuses[url]["timestamp"] = 0
                finally:
                    if driver:
                        try:
                            driver.quit()
                        except:
                            pass
                            
            print("[Background Checker] Check cycle completed.")
        except Exception as e:
            print(f"[Background Checker] Error in worker cycle: {e}")
            time.sleep(5)


def append_log(line):
    global scraper_logs
    with logs_lock:
        scraper_logs.append(line)
        # Keep logs under 10000 lines
        if len(scraper_logs) > 10000:
            scraper_logs = scraper_logs[-10000:]

def log_reader_thread(process):
    global scraper_status, scraper_process
    append_log("--- SCRAPER STARTED ---\n")
    
    # Read output line by line
    while True:
        line = process.stdout.readline()
        if not line:
            break
        append_log(line.decode("utf-8", errors="replace"))
        
    process.stdout.close()
    return_code = process.wait()
    
    with status_lock:
        scraper_status = "idle"
        scraper_process = None
        
    append_log(f"\n--- SCRAPER FINISHED with exit code {return_code} ---\n")

def start_scraper_process(params=None):
    """Starts the scraper subprocess. ASSUMES status_lock IS HELD BY THE CALLER."""
    global scraper_process, scraper_status, scraper_logs
    if params is None:
        params = {}
        
    # Clear previous logs
    with logs_lock:
        scraper_logs.clear()
        
    targets = params.get("targets", {})
    if getattr(sys, 'frozen', False):
        # In a PyInstaller executable, run the executable itself with --run-scraper
        cmd = [sys.executable, "--run-scraper", "--test-run"]
    else:
        scraper_script = os.path.join(DIRECTORY, "live_scraper.py")
        cmd = [sys.executable, "-u", scraper_script, "--test-run"]

    if targets:
        cmd.extend(["--targets", json.dumps(targets)])
    else:
        platforms = params.get("platforms", [])
        channels = params.get("channels", [])
        if platforms:
            cmd.extend(["--platforms"] + platforms)
        if channels:
            cmd.extend(["--channels"] + channels)
            
    scraper_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=EXTERNAL_DIR
    )
    scraper_status = "running"
    
    # Start thread to read logs
    t = threading.Thread(target=log_reader_thread, args=(scraper_process,), daemon=True)
    t.start()

def scheduler_thread_func():
    global scraper_status, next_scheduled_run_time, current_scheduler_interval, scheduler_active, scheduler_targets
    print("[Scheduler] Scheduler thread started.")
    
    while True:
        time.sleep(1)
        
        if not scheduler_active or current_scheduler_interval is None or current_scheduler_interval <= 0:
            continue
            
        now = time.time()
        
        # If next_scheduled_run_time is not initialized yet, reset target grid
        if next_scheduled_run_time is None:
            next_scheduled_run_time = now + current_scheduler_interval
            
        if now >= next_scheduled_run_time:
            with status_lock:
                if scraper_status == "idle":
                    print(f"[Scheduler] Triggering scheduled scraper run for slot {next_scheduled_run_time} (interval: {current_scheduler_interval}s)...")
                    try:
                        start_scraper_process(scheduler_targets)
                        next_scheduled_run_time += current_scheduler_interval
                    except Exception as e:
                        print(f"[Scheduler] Error starting scheduled run: {e}")
                else:
                    # Current run is not finished, so we postpone the next run.
                    # We do not advance next_scheduled_run_time, meaning now >= next_scheduled_run_time
                    # will continue to be True, and it will trigger immediately in the next
                    # loop iteration when scraper_status becomes "idle".
                    pass

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Prevent spamming the console with standard HTTP request logging
        pass

    def do_GET(self):
        global scraper_status, scraper_logs
        
        # API endpoints
        if self.path == "/api/config":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self.wfile.write(f.read().encode("utf-8"))
            else:
                self.wfile.write(json.dumps({"channels": {}, "google_sheet_id": "", "token_path": ""}).encode("utf-8"))
            return
            
        elif self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            # Read scheduler configuration from config.json to display config inputs correctly
            sch_hours = 0
            sch_minutes = 10
            sch_seconds = 0
            try:
                if os.path.exists(CONFIG_PATH):
                    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                        scheduler_config = config_data.get("scheduler", {})
                        sch_hours = int(scheduler_config.get("hours", 0))
                        sch_minutes = int(scheduler_config.get("minutes", 10))
                        sch_seconds = int(scheduler_config.get("seconds", 0))
            except:
                pass
                
            next_run_in = 0
            if scheduler_active and next_scheduled_run_time is not None:
                next_run_in = max(0, int(next_scheduled_run_time - time.time()))

            response_data = {
                "status": scraper_status,
                "scheduler": {
                    "active": scheduler_active,
                    "hours": sch_hours,
                    "minutes": sch_minutes,
                    "seconds": sch_seconds,
                    "next_run_in": next_run_in
                }
            }
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
            return
            
        elif self.path == "/api/link_statuses":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with status_cache_lock:
                self.wfile.write(json.dumps(link_statuses).encode("utf-8"))
            return
            
        elif self.path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            with logs_lock:
                logs_text = "".join(scraper_logs)
            self.wfile.write(json.dumps({"logs": logs_text}).encode("utf-8"))
            return
            
        # Serve static assets
        clean_path = self.path.split('?')[0].rstrip('/')
        if clean_path == "" or clean_path == "/":
            file_path = os.path.join(DIRECTORY, "index.html")
            content_type = "text/html"
        elif clean_path == "/index.css":
            file_path = os.path.join(DIRECTORY, "index.css")
            content_type = "text/css"
        elif clean_path == "/index.js":
            file_path = os.path.join(DIRECTORY, "index.js")
            content_type = "text/javascript"
        else:
            self.send_error(404, "File Not Found")
            return
            
        if os.path.exists(file_path):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "File Not Found")
            return

    def do_POST(self):
        global scraper_process, scraper_status, scraper_logs, scheduler_active, next_scheduled_run_time, current_scheduler_interval, scheduler_targets
        
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        
        if self.path == "/api/heartbeat":
            global last_heartbeat_time
            last_heartbeat_time = time.time()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            return
            
        if self.path == "/api/shutdown":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            
            # Run cleanup and shut down cleanly in a background thread after responding
            def shutdown():
                time.sleep(0.5)
                try:
                    cleanup_gui_app()
                except:
                    pass
                os.kill(os.getpid(), signal.SIGTERM)
                
            threading.Thread(target=shutdown, daemon=True).start()
            return
            
        if self.path == "/api/config":
            try:
                # Validate JSON config
                parsed_config = json.loads(post_data)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(parsed_config, f, indent=2)
                
                # Wake up background checker to check any newly added/updated links
                refresh_status_event.set()
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif self.path == "/api/run":
            with status_lock:
                if scraper_status == "running":
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Scraper is already running"}).encode("utf-8"))
                    return
                
                try:
                    # Parse selected platforms/channels from POST data
                    params = {}
                    if post_data:
                        try:
                            params = json.loads(post_data)
                        except Exception:
                            pass
                    
                    interval = 0
                    try:
                        if os.path.exists(CONFIG_PATH):
                            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                                config_data = json.load(f)
                                scheduler_config = config_data.get("scheduler", {})
                                h = int(scheduler_config.get("hours", 0))
                                m = int(scheduler_config.get("minutes", 0))
                                s = int(scheduler_config.get("seconds", 0))
                                interval = h * 3600 + m * 60 + s
                    except:
                        pass
                        
                    if interval > 0:
                        scheduler_active = True
                        current_scheduler_interval = interval
                        next_scheduled_run_time = time.time() + interval
                        scheduler_targets = params
                    else:
                        scheduler_active = False
                        current_scheduler_interval = None
                        next_scheduled_run_time = None
                        scheduler_targets = None
                    
                    start_scraper_process(params)
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Failed to start scraper: {e}"}).encode("utf-8"))
            return
            
        elif self.path == "/api/scheduler/update_targets":
            try:
                params = json.loads(post_data)
                with status_lock:
                    scheduler_targets = params
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"Failed to update scheduler targets: {e}"}).encode("utf-8"))
            return
            
        elif self.path == "/api/stop":
            with status_lock:
                if scraper_status == "idle" or not scraper_process:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Scraper is not running"}).encode("utf-8"))
                    return
                
                try:
                    # Disable scheduler on user stop
                    scheduler_active = False
                    next_scheduled_run_time = None
                    current_scheduler_interval = None
                    scheduler_targets = None
                    
                    append_log("\n--- TERMINATING SCRAPER BY USER REQUEST ---\n")
                    scraper_process.terminate()
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": f"Failed to stop scraper: {e}"}).encode("utf-8"))
            return
            
        elif self.path.startswith("/api/open_login_browser"):
            # Parse platform from query parameters
            platform = "tiktok"
            from urllib.parse import urlparse, parse_qs
            try:
                parsed_url = urlparse(self.path)
                queries = parse_qs(parsed_url.query)
                if "platform" in queries:
                    platform = queries["platform"][0]
            except:
                pass
                
            # Launch visible login helper Chrome browser
            global active_login_driver
            if 'active_login_driver' not in globals():
                active_login_driver = None
                
            if active_login_driver is not None:
                try:
                    active_login_driver.title
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "error", "message": "A login helper window is already open!"}).encode("utf-8"))
                    return
                except:
                    active_login_driver = None
                    
            def run_browser():
                global active_login_driver
                try:
                    from selenium import webdriver
                    from selenium.webdriver.chrome.options import Options
                    
                    chrome_options = Options()
                    # Visible (non-headless)
                    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
                    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
                    chrome_options.add_experimental_option('useAutomationExtension', False)
                    chrome_options.add_argument("--lang=en-US")
                    chrome_options.add_argument("--mute-audio")
                    
                    base_dir = EXTERNAL_DIR
                    profile_dir = os.path.join(base_dir, "chrome_profile", "MainThread")
                    chrome_options.add_argument(f"--user-data-dir={profile_dir}")
                    
                    driver = webdriver.Chrome(options=chrome_options)
                    active_login_driver = driver
                    
                    if platform == "tiktok":
                        driver.get("https://www.tiktok.com/")
                    elif platform == "facebook":
                        driver.get("https://www.facebook.com/")
                    elif platform == "youtube":
                        driver.get("https://www.youtube.com/")
                    else:
                        driver.get("https://www.google.com")
                        
                    print(f"[Login Helper] Opened visible Chrome window for {platform}.")
                    
                    while True:
                        time.sleep(1)
                        try:
                            _ = driver.title
                        except:
                            break
                    print("[Login Helper] Visible Chrome window closed by user.")
                except Exception as browser_err:
                    print(f"[Login Helper] Error: {browser_err}")
                finally:
                    try:
                        driver.quit()
                    except:
                        pass
                    active_login_driver = None
                    
            t = threading.Thread(target=run_browser)
            t.daemon = True
            t.start()
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": "Login helper opened."}).encode("utf-8"))
            return
            
        elif self.path == "/api/refresh_link_statuses":
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                with status_cache_lock:
                    for channel_name, platform_map in config_data.get("channels", {}).items():
                        for platform in ["youtube", "facebook", "tiktok", "x"]:
                            rows = platform_map.get(platform, [])
                            for row in rows:
                                url = row.get("url", "").strip() if isinstance(row, dict) else row.strip()
                                if url:
                                    link_statuses[url] = { "status": "checking", "viewers": 0, "timestamp": time.time() }
                refresh_status_event.set()
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif self.path == "/api/scan":
            try:
                params = json.loads(post_data)
                channel_name = params.get("channelName")
                platform = params.get("platform")
                keyword = params.get("keyword", "")
                
                if not channel_name or not platform:
                    raise ValueError("channelName and platform are required parameters")
                
                # Load config to find default URL
                if not os.path.exists(CONFIG_PATH):
                    raise ValueError("config.json not found")
                
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                
                channel_config = config_data.get("channels", {}).get(channel_name, {})
                default_urls = channel_config.get("default_urls", {})
                default_url = default_urls.get(platform)
                
                if not default_url:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": f"Default URL for platform '{platform}' not configured for channel '{channel_name}'"
                    }).encode("utf-8"))
                    return
                
                # Run the scan using webdriver
                from live_scraper import setup_stealth_driver, scan_youtube_for_keyword, scan_facebook_for_keyword, scan_tiktok_for_keyword, scan_x_for_keyword
                
                driver = None
                try:
                    driver = setup_stealth_driver()
                    if platform == "youtube":
                        matches = scan_youtube_for_keyword(driver, default_url, keyword)
                    elif platform == "facebook":
                        matches = scan_facebook_for_keyword(driver, default_url, keyword)
                    elif platform == "tiktok":
                        matches = scan_tiktok_for_keyword(driver, default_url, keyword)
                    elif platform == "x":
                        matches = scan_x_for_keyword(driver, default_url, keyword)
                    else:
                        raise ValueError(f"Unknown platform: {platform}")
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": True,
                        "matches": matches
                    }).encode("utf-8"))
                except Exception as e:
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        "success": False,
                        "error": f"Scan error: {str(e)}"
                    }).encode("utf-8"))
                finally:
                    if driver:
                        try:
                            driver.quit()
                        except:
                            pass
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "success": False,
                    "error": str(e)
                }).encode("utf-8"))
            return
            
        self.send_error(404, "Not Found")

def run():
    print(f"Starting server on port {PORT}...")
    print(f"Project Directory: {DIRECTORY}")
    print(f"DISABLE_BROWSER: {os.environ.get('DISABLE_BROWSER')}")
    print(f"DISABLE_HEARTBEAT: {os.environ.get('DISABLE_HEARTBEAT')}")
    
    # Start background status checker
    checker_thread = threading.Thread(target=status_checker_worker, daemon=True)
    checker_thread.start()
    
    # Start background scheduler
    scheduler_thread = threading.Thread(target=scheduler_thread_func, daemon=True)
    scheduler_thread.start()
    
    # Start thread to open web browser in App Mode
    import webbrowser
    import platform
    def open_in_app_mode():
        global dashboard_browser_process
        time.sleep(1.0)
        url = f"http://localhost:{PORT}"
        system = platform.system()
        
        proc = None
        try:
            if system == "Darwin":  # macOS
                profile_dir = os.path.join(EXTERNAL_DIR, "chrome_profile", "Dashboard")
                proc = subprocess.Popen([
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", 
                    f"--app={url}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run"
                ])
            elif system == "Windows":  # Windows
                profile_dir = os.path.join(EXTERNAL_DIR, "chrome_profile", "Dashboard")
                chrome_path = "chrome.exe"
                for path_var in ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]:
                    val = os.environ.get(path_var)
                    if val:
                        p = os.path.join(val, "Google\\Chrome\\Application\\chrome.exe")
                        if os.path.exists(p):
                            chrome_path = p
                            break
                proc = subprocess.Popen([
                    chrome_path, 
                    f"--app={url}",
                    f"--user-data-dir={profile_dir}",
                    "--no-first-run"
                ])
            else:
                webbrowser.open(url)
        except Exception as e:
            # Fallback to standard tab if Chrome App Mode fails
            webbrowser.open(url)
            return

        if proc:
            dashboard_browser_process = proc

    # Heartbeat watcher thread to auto-shutdown when browser window is closed
    def heartbeat_watcher():
        global last_heartbeat_time
        time.sleep(12)  # Allow 12 seconds for the dashboard to launch and send its initial heartbeat
        while True:
            time.sleep(2)
            if time.time() - last_heartbeat_time > 12:
                print("No heartbeat received from dashboard browser window. Shutting down server...")
                try:
                    cleanup_gui_app()
                except:
                    pass
                os.kill(os.getpid(), signal.SIGTERM)
                break

    if os.environ.get("DISABLE_BROWSER") != "1":
        threading.Thread(target=open_in_app_mode, daemon=True).start()
    if os.environ.get("DISABLE_HEARTBEAT") != "1":
        threading.Thread(target=heartbeat_watcher, daemon=True).start()
    
    server_address = ("127.0.0.1", PORT)
    try:
        httpd = HTTPServer(server_address, DashboardHandler)
    except OSError as e:
        # 48 = macOS/Linux address already in use, 98 = Linux alternative, 10048 = Windows address already in use
        if e.errno in (48, 98, 10048) or "already in use" in str(e):
            print("Server is already running. Opening dashboard window...")
            import platform
            system = platform.system()
            url = f"http://localhost:{PORT}"
            try:
                if system == "Darwin":
                    profile_dir = os.path.join(EXTERNAL_DIR, "chrome_profile", "Dashboard")
                    subprocess.Popen([
                        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", 
                        f"--app={url}",
                        f"--user-data-dir={profile_dir}",
                        "--no-first-run"
                    ])
                elif system == "Windows":
                    profile_dir = os.path.join(EXTERNAL_DIR, "chrome_profile", "Dashboard")
                    chrome_path = "chrome.exe"
                    for path_var in ["ProgramFiles", "ProgramFiles(x86)", "LocalAppData"]:
                        val = os.environ.get(path_var)
                        if val:
                            p = os.path.join(val, "Google\\Chrome\\Application\\chrome.exe")
                            if os.path.exists(p):
                                chrome_path = p
                                break
                    subprocess.Popen([
                        chrome_path, 
                        f"--app={url}",
                        f"--user-data-dir={profile_dir}",
                        "--no-first-run"
                    ])
                else:
                    import webbrowser
                    webbrowser.open(url)
            except:
                import webbrowser
                webbrowser.open(url)
            sys.exit(0)
        else:
            raise e

    print(f"GUI Dashboard is available at http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        httpd.server_close()

if __name__ == "__main__":

    if len(sys.argv) > 1 and sys.argv[1] == "--run-scraper":
        import live_scraper
        sys.argv.remove("--run-scraper")
        live_scraper.main()
    else:
        run()
