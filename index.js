// Application State
let appConfig = {
  channels: {},
  google_sheet_id: "",
  token_path: ""
};

// Temp subrows state for editing (maps channels to flat arrays of {platform, url})
let localChannels = {};
// Store default_urls per channel to survive saves
let localDefaultUrls = {};
// Cache of status checker results to prevent resetting badges to "checking" on render
let localLinkStatuses = {};

let activeModalTarget = null; // { channelName, rowIndex }
let activeSettingsChannel = null;
let activeDumpChannel = null;
let logInterval = null;

// DOM Elements
const statusBadge = document.getElementById("status-badge");
const btnRun = document.getElementById("btn-run");
const btnStop = document.getElementById("btn-stop");
const googleSheetIdInput = document.getElementById("google-sheet-id");
const tokenPathInput = document.getElementById("token-path");
const scrapeCommentsEnabledInput = document.getElementById("scrape-comments-enabled");
const commentScrapeDurationInput = document.getElementById("comment-scrape-duration");
const maxCheckingWorkersInput = document.getElementById("max-checking-workers");
const maxCommentWorkersInput = document.getElementById("max-comment-workers");
const usePersistentProfileInput = document.getElementById("use-persistent-profile");
const loginHelperPlatformSelect = document.getElementById("login-helper-platform");
const btnOpenLogin = document.getElementById("btn-open-login");
const btnSaveConfig = document.getElementById("btn-save-config");
const btnQuitApp = document.getElementById("btn-quit-app");
const btnAddChannel = document.getElementById("btn-add-channel");
const channelsGrid = document.getElementById("channels-grid");
const terminalOutput = document.getElementById("terminal-output");
const btnClearLogs = document.getElementById("btn-clear-logs");

// Scheduler DOM Elements
const schedulerHoursInput = document.getElementById("scheduler-hours");
const schedulerMinutesInput = document.getElementById("scheduler-minutes");
const schedulerSecondsInput = document.getElementById("scheduler-seconds");
const schedulerStatusText = document.getElementById("scheduler-status-text");
const schedulerAutoAddSelect = document.getElementById("scheduler-auto-add");

// Scheduler Update Modal Elements
const schedulerUpdateModal = document.getElementById("scheduler-update-modal");
const newPlatformsList = document.getElementById("new-platforms-list");
const selectSchedulerBehavior = document.getElementById("select-scheduler-behavior");
const btnCancelSchedulerUpdate = document.getElementById("btn-cancel-scheduler-update");
const btnConfirmSchedulerUpdate = document.getElementById("btn-confirm-scheduler-update");
const btnCloseSchedulerUpdateModal = document.getElementById("btn-close-scheduler-update-modal");

// State for auto-add comparison
let lastRunTargets = null;
let configSnapshotAtStart = null;
let pendingSchedulerTargetsUpdate = null;

// Modal Elements
const urlModal = document.getElementById("url-modal");
const modalTitle = document.getElementById("modal-title");
const modalUrlInput = document.getElementById("modal-url-input");
const btnCloseModal = document.getElementById("btn-close-modal");
const btnCancelModal = document.getElementById("btn-cancel-modal");
const btnSaveUrl = document.getElementById("btn-save-url");

// Channel Settings Modal Elements
const channelSettingsModal = document.getElementById("channel-settings-modal");
const settingsTitle = document.getElementById("channel-settings-title");
const inputDefaultYoutube = document.getElementById("settings-default-youtube");
const inputDefaultFacebook = document.getElementById("settings-default-facebook");
const inputDefaultTiktok = document.getElementById("settings-default-tiktok");
const inputDefaultX = document.getElementById("settings-default-x");
const btnCloseSettingsModal = document.getElementById("btn-close-settings-modal");
const btnCancelSettingsModal = document.getElementById("btn-cancel-settings-modal");
const btnSaveSettings = document.getElementById("btn-save-settings");

// Run Channels Modal Elements
const runChannelsModal = document.getElementById("run-channels-modal");
const runChannelsList = document.getElementById("run-channels-list");
const btnCloseRunModal = document.getElementById("btn-close-run-modal");
const btnCancelRunModal = document.getElementById("btn-cancel-run-modal");
const btnConfirmRun = document.getElementById("btn-confirm-run");
const btnSelectAll = document.getElementById("btn-select-all");
const btnDeselectAll = document.getElementById("btn-deselect-all");

// Rearrange Modal Elements
const btnReorderChannels = document.getElementById("btn-reorder-channels");
const reorderModal = document.getElementById("reorder-modal");
const reorderList = document.getElementById("reorder-list");
const btnCloseReorderModal = document.getElementById("btn-close-reorder-modal");
const btnCancelReorderModal = document.getElementById("btn-cancel-reorder-modal");
const btnSaveReorder = document.getElementById("btn-save-reorder");

// URL Dump Modal Elements
const urlDumpModal = document.getElementById("url-dump-modal");
const btnCloseUrlDumpModal = document.getElementById("btn-close-url-dump-modal");
const btnCancelUrlDump = document.getElementById("btn-cancel-url-dump");
const btnImportUrlDump = document.getElementById("btn-import-url-dump");

// --- INITIALIZATION ---
window.addEventListener("DOMContentLoaded", () => {
  fetchConfig();
  checkStatus();
  setInterval(checkStatus, 3000); // Check status every 3 seconds to keep UI and scheduler countdown updated
  startStatusPolling();
  
  // Start heartbeat to keep server alive while dashboard is open
  const startHeartbeat = () => {
    fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
    setInterval(() => {
      fetch("/api/heartbeat", { method: "POST" }).catch(() => {});
    }, 3000);
  };
  startHeartbeat();
  
  // Setup Event Listeners
  const btnCheckNow = document.getElementById("btn-check-now");
  if (btnCheckNow) {
    btnCheckNow.addEventListener("click", triggerManualStatusCheck);
  }
  
  btnRun.addEventListener("click", openRunChannelsModal);
  btnStop.addEventListener("click", stopScraper);
  btnSaveConfig.addEventListener("click", saveConfig);
  if (btnQuitApp) {
    btnQuitApp.addEventListener("click", () => {
      if (confirm("Are you sure you want to stop the server and quit the application completely?")) {
        fetch("/api/shutdown", { method: "POST" })
          .then(res => res.json())
          .then(data => {
            if (data.success) {
              alert("Application is shutting down. You can close this window now.");
              window.close();
            } else {
              alert("Failed to shut down server.");
            }
          })
          .catch(err => {
            console.error("Error shutting down:", err);
            alert("Application is shutting down. You can close this window now.");
            window.close();
          });
      }
    });
  }
  const toggleCommentDurationVisibility = () => {
    const group = document.getElementById("comment-duration-group");
    if (group) {
      group.style.display = scrapeCommentsEnabledInput.checked ? "block" : "none";
    }
  };
  scrapeCommentsEnabledInput.addEventListener("change", () => {
    toggleCommentDurationVisibility();
    saveConfig(false);
  });
  commentScrapeDurationInput.addEventListener("change", () => {
    let val = parseInt(commentScrapeDurationInput.value) || 30;
    if (val < 1) {
      commentScrapeDurationInput.value = 1;
    }
    saveConfig(false);
  });
  maxCheckingWorkersInput.addEventListener("change", () => {
    let val = parseInt(maxCheckingWorkersInput.value);
    if (isNaN(val)) val = 6;
    if (val < 0) val = 0;
    if (val > 48) val = 48;
    maxCheckingWorkersInput.value = val;
    saveConfig(false);
  });
  maxCommentWorkersInput.addEventListener("change", () => {
    let val = parseInt(maxCommentWorkersInput.value);
    if (isNaN(val)) val = 5;
    if (val < 0) val = 0;
    if (val > 48) val = 48;
    maxCommentWorkersInput.value = val;
    saveConfig(false);
  });
  
  // Scheduler Event Listeners
  const schedulerSaveHandler = () => {
    let h = parseInt(schedulerHoursInput.value) || 0;
    let m = parseInt(schedulerMinutesInput.value) || 0;
    let s = parseInt(schedulerSecondsInput.value) || 0;
    if (h < 0) schedulerHoursInput.value = 0;
    if (m < 0) schedulerMinutesInput.value = 0;
    if (s < 0) schedulerSecondsInput.value = 0;
    saveConfig(false);
  };
  schedulerHoursInput.addEventListener("change", schedulerSaveHandler);
  schedulerMinutesInput.addEventListener("change", schedulerSaveHandler);
  schedulerSecondsInput.addEventListener("change", schedulerSaveHandler);
  if (schedulerAutoAddSelect) {
    schedulerAutoAddSelect.addEventListener("change", () => saveConfig(false));
  }
  
  // Scheduler Update Modal Event Listeners
  if (btnConfirmSchedulerUpdate) {
    btnConfirmSchedulerUpdate.addEventListener("click", () => {
      if (pendingSchedulerTargetsUpdate) {
        updateSchedulerTargetsOnBackend(pendingSchedulerTargetsUpdate);
        lastRunTargets = pendingSchedulerTargetsUpdate;
      }
      
      // Update configSnapshotAtStart to match new state
      configSnapshotAtStart = getCurrentConfiguredTargets();
      
      const newBehavior = selectSchedulerBehavior.value;
      if (newBehavior !== appConfig.scheduler.auto_add_new_targets) {
        schedulerAutoAddSelect.value = newBehavior;
        saveConfig(false);
      }
      closeSchedulerUpdateModal();
    });
  }
  
  if (btnCancelSchedulerUpdate) {
    btnCancelSchedulerUpdate.addEventListener("click", () => {
      // Set to current targets mapping to consider them "processed" and avoid repeating prompt
      configSnapshotAtStart = getCurrentConfiguredTargets();
      lastRunTargets = getCurrentConfiguredTargets();

      const newBehavior = selectSchedulerBehavior.value;
      if (newBehavior !== appConfig.scheduler.auto_add_new_targets) {
        schedulerAutoAddSelect.value = newBehavior;
        saveConfig(false);
      }
      closeSchedulerUpdateModal();
    });
  }
  
  if (btnCloseSchedulerUpdateModal) {
    btnCloseSchedulerUpdateModal.addEventListener("click", closeSchedulerUpdateModal);
  }
  
  btnAddChannel.addEventListener("click", addNewChannel);
  btnClearLogs.addEventListener("click", () => {
    terminalOutput.innerHTML = '<span class="terminal-placeholder">Logs cleared. Running scraper will show fresh output...</span>';
  });
  
  // Modal Event Listeners
  btnCloseModal.addEventListener("click", closeModal);
  btnCancelModal.addEventListener("click", closeModal);
  btnSaveUrl.addEventListener("click", saveModalUrl);
  
  // Channel Settings Event Listeners
  if (btnCloseSettingsModal) btnCloseSettingsModal.addEventListener("click", closeChannelSettingsModal);
  if (btnCancelSettingsModal) btnCancelSettingsModal.addEventListener("click", closeChannelSettingsModal);
  if (btnSaveSettings) btnSaveSettings.addEventListener("click", saveChannelSettings);
  
  // Run Channels Modal Event Listeners
  if (btnCloseRunModal) btnCloseRunModal.addEventListener("click", closeRunChannelsModal);
  if (btnCancelRunModal) btnCancelRunModal.addEventListener("click", closeRunChannelsModal);
  if (btnConfirmRun) btnConfirmRun.addEventListener("click", runScraper);
  if (btnSelectAll) btnSelectAll.addEventListener("click", selectAllChannels);
  if (btnDeselectAll) btnDeselectAll.addEventListener("click", deselectAllChannels);
  
  // Rearrange Modal Event Listeners
  if (btnReorderChannels) btnReorderChannels.addEventListener("click", openReorderModal);
  if (btnCloseReorderModal) btnCloseReorderModal.addEventListener("click", closeReorderModal);
  if (btnCancelReorderModal) btnCancelReorderModal.addEventListener("click", closeReorderModal);
  if (btnSaveReorder) btnSaveReorder.addEventListener("click", saveReorderedChannels);
  
  if (reorderList) {
    reorderList.addEventListener("dragover", handleReorderDragOver);
  }
  
  // Keyword scan button
  const btnScanLive = document.getElementById("btn-scan-live");
  if (btnScanLive) btnScanLive.addEventListener("click", runLiveScan);
  
  // URL Dump Modal Event Listeners
  if (btnCloseUrlDumpModal) btnCloseUrlDumpModal.addEventListener("click", closeUrlDumpModal);
  if (btnCancelUrlDump) btnCancelUrlDump.addEventListener("click", closeUrlDumpModal);
  if (btnImportUrlDump) btnImportUrlDump.addEventListener("click", importDumpedUrls);

  // Open URL Modal Link button
  const btnOpenModalUrl = document.getElementById("btn-open-modal-url");
  if (btnOpenModalUrl) {
    btnOpenModalUrl.addEventListener("click", () => {
      const url = modalUrlInput.value.trim();
      if (url) {
        window.open(url, "_blank");
      } else {
        alert("Please enter or select a URL first.");
      }
    });
  }
  
  // Login Helper Open Chrome button
  if (btnOpenLogin) {
    btnOpenLogin.addEventListener("click", () => {
      const platform = loginHelperPlatformSelect ? loginHelperPlatformSelect.value : "tiktok";
      appendLogLine(`[Login Helper] Requesting to open Chrome login window for ${platform.toUpperCase()}...\n`);
      btnOpenLogin.disabled = true;
      fetch(`/api/open_login_browser?platform=${platform}`, { method: "POST" })
        .then(res => res.json())
        .then(data => {
          btnOpenLogin.disabled = false;
          if (data.status === "success") {
            appendLogLine(`[Login Helper] Chrome window opened. Please solve any captchas or log in. Close it when you are done.\n`);
          } else {
            alert(data.message || "Failed to open Chrome window.");
            appendLogLine(`[Login Helper] Error: ${data.message}\n`);
          }
        })
        .catch(err => {
          btnOpenLogin.disabled = false;
          alert("Error opening Chrome window: " + err);
          appendLogLine(`[Login Helper] Error: ${err}\n`);
        });
    });
  }
});

// --- API ACTIONS ---

// Fetch config from server
function fetchConfig() {
  fetch("/api/config")
    .ajaxLoad(res => res.json())
    .then(data => {
      appConfig = data;
      googleSheetIdInput.value = data.google_sheet_id || "";
      tokenPathInput.value = data.token_path || "";
      scrapeCommentsEnabledInput.checked = data.scrape_comments !== false;
      commentScrapeDurationInput.value = data.comment_scrape_duration_seconds !== undefined ? data.comment_scrape_duration_seconds : 30;
      maxCheckingWorkersInput.value = data.max_checking_workers !== undefined ? data.max_checking_workers : 6;
      maxCommentWorkersInput.value = data.max_comment_workers !== undefined ? data.max_comment_workers : 5;
      if (usePersistentProfileInput) {
        usePersistentProfileInput.checked = data.use_persistent_chrome_profile === true;
      }
      
      // Initialize visibility of the duration input field
      const group = document.getElementById("comment-duration-group");
      if (group) {
        group.style.display = scrapeCommentsEnabledInput.checked ? "block" : "none";
      }
      
      // Populate Scheduler Inputs
      if (data.scheduler) {
        schedulerHoursInput.value = data.scheduler.hours !== undefined ? data.scheduler.hours : 0;
        schedulerMinutesInput.value = data.scheduler.minutes !== undefined ? data.scheduler.minutes : 10;
        schedulerSecondsInput.value = data.scheduler.seconds !== undefined ? data.scheduler.seconds : 0;
        if (schedulerAutoAddSelect) {
          schedulerAutoAddSelect.value = data.scheduler.auto_add_new_targets || "ask";
        }
      } else {
        schedulerHoursInput.value = 0;
        schedulerMinutesInput.value = 10;
        schedulerSecondsInput.value = 0;
        if (schedulerAutoAddSelect) {
          schedulerAutoAddSelect.value = "ask";
        }
      }
      
      // Parse channels dict into a flat array structure for easy UI mapping
      localChannels = {};
      localDefaultUrls = {};
      for (const channelName in data.channels) {
        localChannels[channelName] = [];
        const platformMap = data.channels[channelName];
        
        // Extract default_urls
        localDefaultUrls[channelName] = platformMap.default_urls || {};
        
        for (const platform in platformMap) {
          if (platform === "default_urls") continue;
          
          const urls = platformMap[platform];
          if (Array.isArray(urls)) {
            urls.forEach(item => {
              if (item && typeof item === "object") {
                localChannels[channelName].push({
                  platform,
                  url: item.url || "",
                  type: item.type || "online"
                });
              } else {
                localChannels[channelName].push({
                  platform,
                  url: item || "",
                  type: "online"
                });
              }
            });
          } else if (typeof urls === "string") {
            localChannels[channelName].push({ platform, url: urls, type: "online" });
          }
        }
      }
      // Initialize configSnapshotAtStart and lastRunTargets to current configured targets if not set yet
      const initialTargets = {};
      for (const channelName in data.channels) {
        const platformsList = [];
        for (const platform in data.channels[channelName]) {
          if (platform === "default_urls") continue;
          const urls = data.channels[channelName][platform];
          if (Array.isArray(urls) && urls.length > 0) {
            platformsList.push(platform);
          }
        }
        if (platformsList.length > 0) {
          initialTargets[channelName] = platformsList;
        }
      }
      if (!configSnapshotAtStart) {
        configSnapshotAtStart = JSON.parse(JSON.stringify(initialTargets));
      }
      if (!lastRunTargets) {
        lastRunTargets = JSON.parse(JSON.stringify(initialTargets));
      }

      renderChannels();
    })
    .catch(err => {
      showTerminalError(`Failed to load config: ${err.message}`);
    });
}

// Helper to support .ajaxLoad or normal fetch promise
Promise.prototype.ajaxLoad = function(cb) {
  return this.then(cb);
};

// Save config to server
function saveConfig(showNotification = true) {
  // Map local flat arrays back to dict format
  const channelsMapped = {};
  for (const channelName in localChannels) {
    channelsMapped[channelName] = {};
    
    // Restore default_urls
    if (localDefaultUrls[channelName] && Object.keys(localDefaultUrls[channelName]).length > 0) {
      channelsMapped[channelName].default_urls = localDefaultUrls[channelName];
    }
    
    localChannels[channelName].forEach(item => {
      if (!channelsMapped[channelName][item.platform]) {
        channelsMapped[channelName][item.platform] = [];
      }
      if (item.url.trim() !== "") {
        channelsMapped[channelName][item.platform].push({
          url: item.url.trim(),
          type: item.type || "online"
        });
      }
    });
  }
  
  // Strip empty arrays (ignoring default_urls)
  for (const ch in channelsMapped) {
    for (const pl in channelsMapped[ch]) {
      if (pl === "default_urls") continue;
      if (channelsMapped[ch][pl].length === 0) {
        delete channelsMapped[ch][pl];
      }
    }
  }

  appConfig.channels = channelsMapped;
  appConfig.google_sheet_id = googleSheetIdInput.value.trim();
  appConfig.token_path = tokenPathInput.value.trim();
  appConfig.scrape_comments = scrapeCommentsEnabledInput.checked;
  appConfig.comment_scrape_duration_seconds = parseInt(commentScrapeDurationInput.value) || 30;
  const parseChecking = parseInt(maxCheckingWorkersInput.value);
  appConfig.max_checking_workers = isNaN(parseChecking) ? 6 : parseChecking;
  const parseComment = parseInt(maxCommentWorkersInput.value);
  appConfig.max_comment_workers = isNaN(parseComment) ? 5 : parseComment;
  if (usePersistentProfileInput) {
    appConfig.use_persistent_chrome_profile = usePersistentProfileInput.checked;
  }
  appConfig.scheduler = {
    hours: parseInt(schedulerHoursInput.value) || 0,
    minutes: parseInt(schedulerMinutesInput.value) || 0,
    seconds: parseInt(schedulerSecondsInput.value) || 0,
    auto_add_new_targets: schedulerAutoAddSelect ? schedulerAutoAddSelect.value : "ask"
  };

  return fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(appConfig)
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        if (showNotification) {
          appendLogLine("System settings saved successfully.\n");
        }
      } else {
        throw new Error(data.error || "Unknown error");
      }
    })
    .catch(err => {
      showTerminalError(`Failed to save config: ${err.message}`);
    });
}

String.prototype.strip = function() {
  return this.trim();
};

// Run Scraper Process
function runScraper() {
  btnRun.disabled = true;
  
  const targets = {};
  const channelGroups = runChannelsList.querySelectorAll(".channel-run-group");
  
  channelGroups.forEach(group => {
    const parentChk = group.querySelector(".chk-channel-parent");
    if (parentChk && parentChk.checked) {
      const channelName = parentChk.value;
      const checkedPlatformChks = group.querySelectorAll(".chk-platform-item:checked");
      const selectedPlatforms = Array.from(checkedPlatformChks).map(chk => chk.value);
      targets[channelName] = selectedPlatforms;
    }
  });
  
  const hasAtLeastOnePlatformToScrape = Object.values(targets).some(platforms => platforms.length > 0);
  const totalChannelsCount = Object.keys(localChannels).length;
  const anyChannelHasPlatforms = Object.values(localChannels).some(r => r.length > 0);
  
  if (anyChannelHasPlatforms && !hasAtLeastOnePlatformToScrape) {
    alert("Please select at least one channel and platform to scrape.");
    btnRun.disabled = false;
    return;
  }
  
  if (Object.keys(targets).length === 0) {
    alert("Please select at least one channel to scrape.");
    btnRun.disabled = false;
    return;
  }

  closeRunChannelsModal();
  appendLogLine("Saving settings and initiating scraper...\n");
  
  configSnapshotAtStart = getCurrentConfiguredTargets();
  lastRunTargets = JSON.parse(JSON.stringify(targets));
  
  saveConfig(false).then(() => {
    fetch("/api/run", { 
      method: "POST", 
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ targets })
    })
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          updateStatusUI("running");
          startLogPolling();
        } else {
          btnRun.disabled = false;
          showTerminalError(`Failed to run scraper: ${data.error}`);
        }
      })
      .catch(err => {
        btnRun.disabled = false;
        showTerminalError(`Failed to run scraper: ${err.message}`);
      });
  });
}

// Stop Scraper Process
function stopScraper() {
  btnStop.disabled = true;
  appendLogLine("Sending termination request to scraper...\n");
  
  fetch("/api/stop", { method: "POST" })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        appendLogLine("Termination signal sent.\n");
      } else {
        btnStop.disabled = false;
        showTerminalError(`Failed to stop scraper: ${data.error}`);
      }
    })
    .catch(err => {
      btnStop.disabled = false;
      showTerminalError(`Failed to stop scraper: ${err.message}`);
    });
}

function triggerManualStatusCheck() {
  const btnCheckNow = document.getElementById("btn-check-now");
  if (btnCheckNow) {
    btnCheckNow.disabled = true;
    btnCheckNow.innerText = "Checking...";
  }
  
  // Instantly set DOM badges to checking state for immediate UI feedback
  const badges = document.querySelectorAll(".link-status-badge");
  badges.forEach(badge => {
    badge.className = "link-status-badge checking";
    badge.title = "Checking status...";
    badge.innerText = "";
  });
  
  fetch("/api/refresh_link_statuses", { method: "POST" })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        // Poll status closely over the next few seconds as results come in
        setTimeout(pollLinkStatuses, 1500);
        setTimeout(pollLinkStatuses, 5000);
        setTimeout(pollLinkStatuses, 10000);
        setTimeout(pollLinkStatuses, 15000);
        setTimeout(pollLinkStatuses, 25000);
      }
    })
    .catch(err => console.error("Error triggering manual check:", err))
    .finally(() => {
      setTimeout(() => {
        if (btnCheckNow) {
          btnCheckNow.disabled = false;
          btnCheckNow.innerText = "Check Now 🔄";
        }
      }, 5000); // Prevent spamming
    });
}

let statusPollInterval = null;

function startStatusPolling() {
  if (statusPollInterval) clearInterval(statusPollInterval);
  pollLinkStatuses();
  statusPollInterval = setInterval(pollLinkStatuses, 30000); // Check every 30 seconds
}

function pollLinkStatuses() {
  fetch("/api/link_statuses")
    .then(res => res.json())
    .then(statuses => {
      localLinkStatuses = statuses; // Save to local cache
      let maxTimestamp = 0;
      const badges = document.querySelectorAll(".link-status-badge");
      badges.forEach(badge => {
        const url = badge.getAttribute("data-url");
        if (url && statuses[url]) {
          const info = statuses[url];
          badge.className = "link-status-badge";
          
          // Only use colored dots without text
          if (info.status === "live") {
            badge.classList.add("live");
            if (info.viewers > 0) {
              badge.title = `Live (${info.viewers} viewers)`;
            } else {
              badge.title = "Live";
            }
          } else if (info.status === "offline") {
            badge.classList.add("offline");
            badge.title = "Offline/Ended";
          } else if (info.status === "checking") {
            badge.classList.add("checking");
            badge.title = "Checking status...";
          } else {
            badge.classList.add("offline");
            badge.title = "Unknown";
          }
          badge.innerText = ""; // No text inside dot
          
          if (info.timestamp > maxTimestamp) {
            maxTimestamp = info.timestamp;
          }
        }
      });
      
      // Update last-checked time
      if (maxTimestamp > 0) {
        const date = new Date(maxTimestamp * 1000);
        const timeStr = date.toLocaleTimeString();
        const lastCheckTimeEl = document.getElementById("last-check-time");
        if (lastCheckTimeEl) {
          lastCheckTimeEl.innerText = timeStr;
        }
      }
    })
    .catch(err => console.error("Error polling link statuses:", err));
}

function getCurrentConfiguredTargets() {
  const targets = {};
  for (const channelName in localChannels) {
    const platformsList = [];
    localChannels[channelName].forEach(item => {
      if (item.url && item.url.trim() !== "") {
        platformsList.push(item.platform);
      }
    });
    if (platformsList.length > 0) {
      targets[channelName] = platformsList;
    }
  }
  return targets;
}

function checkAndHandleNewTargets() {
  if (!lastRunTargets) return;
  
  const currentConfiguredTargets = getCurrentConfiguredTargets();
  
  const addedTargets = [];
  const newTargetsJson = JSON.parse(JSON.stringify(lastRunTargets));
  let hasAddedAny = false;
  
  for (const channelName in currentConfiguredTargets) {
    const currentPlatforms = currentConfiguredTargets[channelName];
    const startPlatforms = (configSnapshotAtStart && configSnapshotAtStart[channelName]) || [];
    
    currentPlatforms.forEach(p => {
      if (!startPlatforms.includes(p)) {
        addedTargets.push(`${channelName} (${p.toUpperCase()})`);
        
        if (!newTargetsJson[channelName]) {
          newTargetsJson[channelName] = [];
        }
        if (!newTargetsJson[channelName].includes(p)) {
          newTargetsJson[channelName].push(p);
        }
        hasAddedAny = true;
      }
    });
  }
  
  if (!hasAddedAny) {
    configSnapshotAtStart = currentConfiguredTargets;
    return;
  }
  
  const behavior = (appConfig.scheduler && appConfig.scheduler.auto_add_new_targets) || "ask";
  
  if (behavior === "never") {
    configSnapshotAtStart = currentConfiguredTargets;
    return;
  }
  
  if (behavior === "always") {
    updateSchedulerTargetsOnBackend(newTargetsJson);
    configSnapshotAtStart = currentConfiguredTargets;
    return;
  }
  
  if (behavior === "ask") {
    newPlatformsList.innerHTML = "";
    addedTargets.forEach(text => {
      const li = document.createElement("li");
      li.innerText = text;
      newPlatformsList.appendChild(li);
    });
    
    selectSchedulerBehavior.value = "ask";
    pendingSchedulerTargetsUpdate = newTargetsJson;
    schedulerUpdateModal.classList.add("open");
  }
}

function updateSchedulerTargetsOnBackend(targetsMapping) {
  fetch("/api/scheduler/update_targets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ targets: targetsMapping })
  })
    .then(res => res.json())
    .then(data => {
      if (data.success) {
        appendLogLine("[Scheduler] Targets successfully updated to include newly configured platforms.\n");
      } else {
        console.error("Failed to update scheduler targets:", data.error);
      }
    })
    .catch(err => console.error("Error updating scheduler targets:", err));
}

function closeSchedulerUpdateModal() {
  schedulerUpdateModal.classList.remove("open");
  pendingSchedulerTargetsUpdate = null;
}

// Check current running status
function checkStatus() {
  fetch("/api/status")
    .then(res => res.json())
    .then(data => {
      updateStatusUI(data.status);
      updateSchedulerUI(data.scheduler, data.status);
      if (data.status === "running") {
        startLogPolling();
      }
    });
}

function updateSchedulerUI(schedulerData, scraperStatus) {
  if (!schedulerData || !schedulerData.active) {
    schedulerStatusText.innerText = "Scheduler: Disabled";
    return;
  }
  
  if (scraperStatus === "running") {
    schedulerStatusText.innerText = "Scheduler: Postponed - waiting for current run...";
    return;
  }
  
  const nextIn = schedulerData.next_run_in;
  if (nextIn <= 0) {
    schedulerStatusText.innerText = "Scheduler: Triggering now...";
  } else {
    const hours = Math.floor(nextIn / 3600);
    const minutes = Math.floor((nextIn % 3600) / 60);
    const seconds = nextIn % 60;
    
    let timeStr = "";
    if (hours > 0) timeStr += `${hours}h `;
    if (minutes > 0 || hours > 0) timeStr += `${minutes}m `;
    timeStr += `${seconds}s`;
    
    schedulerStatusText.innerText = `Scheduler: Active (Next run in ${timeStr})`;
  }
}

// --- LOG POLLING ---
function startLogPolling() {
  if (logInterval) clearInterval(logInterval);
  logInterval = setInterval(() => {
    fetch("/api/logs")
      .then(res => res.json())
      .then(data => {
        if (data.logs) {
          terminalOutput.innerText = data.logs;
          terminalOutput.scrollTop = terminalOutput.scrollHeight;
        }
      });
      
    // Verify status periodically
    fetch("/api/status")
      .then(res => res.json())
      .then(data => {
        updateSchedulerUI(data.scheduler, data.status);
        if (data.status === "idle") {
          updateStatusUI("idle");
          clearInterval(logInterval);
          logInterval = null;
          checkAndHandleNewTargets();
        }
      });
  }, 1000);
}

// --- UI UPDATES ---

function updateStatusUI(status) {
  if (status === "running") {
    statusBadge.innerText = "Running";
    statusBadge.className = "badge running";
    btnRun.disabled = true;
    btnRun.classList.add("active");
    btnStop.disabled = false;
  } else {
    statusBadge.innerText = "Idle";
    statusBadge.className = "badge idle";
    btnRun.disabled = false;
    btnRun.classList.remove("active");
    btnStop.disabled = true;
  }
}

function renderChannels() {
  channelsGrid.innerHTML = "";
  
  for (const channelName in localChannels) {
    const card = document.createElement("div");
    card.className = "card channel-card";
    
    // Header
    const header = document.createElement("div");
    header.className = "channel-header";
    
    const titleInput = document.createElement("input");
    titleInput.type = "text";
    titleInput.className = "channel-title-input";
    titleInput.value = channelName;
    titleInput.addEventListener("change", (e) => renameChannel(channelName, e.target.value.trim()));
    
    const btnSettings = document.createElement("button");
    btnSettings.className = "btn-icon-only btn-settings-cog";
    btnSettings.innerHTML = "⚙️";
    btnSettings.title = "Channel Default URLs";
    btnSettings.addEventListener("click", () => openChannelSettings(channelName));
    
    const btnDump = document.createElement("button");
    btnDump.className = "btn-icon-only btn-url-dump";
    btnDump.innerHTML = "📥";
    btnDump.title = "Dump / Import URLs";
    btnDump.addEventListener("click", () => openUrlDump(channelName));
    
    const btnDeleteCh = document.createElement("button");
    btnDeleteCh.className = "btn-icon-only";
    btnDeleteCh.innerHTML = "🗑";
    btnDeleteCh.title = "Delete Channel";
    btnDeleteCh.addEventListener("click", () => deleteChannel(channelName));
    
    header.appendChild(titleInput);
    header.appendChild(btnSettings);
    header.appendChild(btnDump);
    header.appendChild(btnDeleteCh);
    card.appendChild(header);
    
    // Sub-rows List
    const subrowsContainer = document.createElement("div");
    subrowsContainer.className = "subrows-list";
    
    const rows = localChannels[channelName];
    rows.forEach((row, rowIndex) => {
      // Auto-populate TikTok URL from default settings if empty
      if (row.platform === "tiktok" && (!row.url || row.url.trim() === "")) {
        const defaults = localDefaultUrls[channelName] || {};
        if (defaults.tiktok) {
          row.url = defaults.tiktok;
          setTimeout(() => saveConfig(true), 0); // Silently save to config
        }
      }

      const rowItem = document.createElement("div");
      rowItem.className = "subrow-item";
      
      // Platform dropdown
      const select = document.createElement("select");
      select.className = "platform-select";
      ["youtube", "facebook", "tiktok", "x"].forEach(p => {
        const opt = document.createElement("option");
        opt.value = p;
        opt.innerText = p.toUpperCase();
        if (p === row.platform) opt.selected = true;
        select.appendChild(opt);
      });
      select.addEventListener("change", (e) => {
        const newPlatform = e.target.value;
        const currentUrl = (row.url || "").trim();
        if (currentUrl !== "") {
          if (newPlatform === "youtube" && !currentUrl.includes("youtube.com") && !currentUrl.includes("youtu.be")) {
            alert("Warning: The configured URL doesn't match a YouTube page format!");
          } else if (newPlatform === "facebook" && !currentUrl.includes("facebook.com")) {
            alert("Warning: The configured URL doesn't match a Facebook page format!");
          } else if (newPlatform === "tiktok" && !currentUrl.includes("tiktok.com")) {
            alert("Warning: The configured URL doesn't match a TikTok page format!");
          } else if (newPlatform === "x" && !currentUrl.includes("x.com") && !currentUrl.includes("twitter.com")) {
            alert("Warning: The configured URL doesn't match an X page format!");
          }
        }
        row.platform = newPlatform;
        if (newPlatform === "tiktok" && (!row.url || row.url.trim() === "")) {
          const defaults = localDefaultUrls[channelName] || {};
          if (defaults.tiktok) {
            row.url = defaults.tiktok;
            renderChannels();
          }
        }
        saveConfig(false);
      });
      
      // Type segmented selector
      const typeContainer = document.createElement("div");
      typeContainer.className = "type-segmented-control";
      
      const btnOnline = document.createElement("button");
      btnOnline.type = "button";
      btnOnline.className = "type-segment-btn" + (row.type === "tv" ? "" : " active");
      btnOnline.innerText = "Online";
      btnOnline.addEventListener("click", () => {
        row.type = "online";
        btnOnline.classList.add("active");
        btnTv.classList.remove("active");
        saveConfig(false);
      });
      
      const btnTv = document.createElement("button");
      btnTv.type = "button";
      btnTv.className = "type-segment-btn" + (row.type === "tv" ? " active" : "");
      btnTv.innerText = "TV";
      btnTv.addEventListener("click", () => {
        row.type = "tv";
        btnTv.classList.add("active");
        btnOnline.classList.remove("active");
        saveConfig(false);
      });
      
      typeContainer.appendChild(btnOnline);
      typeContainer.appendChild(btnTv);
      
      // Configure button
      const btnConfig = document.createElement("button");
      btnConfig.className = "btn-link-config";
      const hasUrl = row.url && row.url.trim() !== "";
      if (hasUrl) {
        btnConfig.classList.add("configured");
        btnConfig.innerText = "Link";
        btnConfig.title = `${row.url}\n(Cmd/Ctrl+Click to open in new tab)`; // Show full URL on hover
      } else {
        btnConfig.innerText = "Setup";
        btnConfig.title = "No link configured. Click to configure.";
      }
      btnConfig.addEventListener("click", (e) => {
        if (hasUrl && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          e.stopPropagation();
          window.open(row.url.trim(), "_blank");
        } else {
          openUrlModal(channelName, rowIndex);
        }
      });
      
      // Status Badge (only if URL is configured)
      let statusBadge = null;
      if (hasUrl) {
        statusBadge = document.createElement("span");
        const urlKey = row.url.trim();
        const cached = localLinkStatuses[urlKey];
        if (cached) {
          statusBadge.className = `link-status-badge ${cached.status}`;
          if (cached.status === "live") {
            statusBadge.title = cached.viewers > 0 ? `Live (${cached.viewers} viewers)` : "Live";
          } else if (cached.status === "offline") {
            statusBadge.title = "Offline/Ended";
          } else {
            statusBadge.title = "Checking status...";
          }
        } else {
          statusBadge.className = "link-status-badge checking";
          statusBadge.title = "Checking status...";
        }
        statusBadge.setAttribute("data-url", urlKey);
        statusBadge.innerText = "";
      }
      
      // Remove row button
      const btnRemoveRow = document.createElement("button");
      btnRemoveRow.className = "btn-icon-only";
      btnRemoveRow.innerHTML = "&times;";
      btnRemoveRow.title = "Remove Row";
      btnRemoveRow.addEventListener("click", () => removeSubrow(channelName, rowIndex));
      
      rowItem.appendChild(select);
      rowItem.appendChild(typeContainer);
      rowItem.appendChild(btnConfig);
      if (statusBadge) {
        rowItem.appendChild(statusBadge);
      }
      rowItem.appendChild(btnRemoveRow);
      subrowsContainer.appendChild(rowItem);
    });
    
    card.appendChild(subrowsContainer);
    
    // Add Row Button
    const btnAddRow = document.createElement("button");
    btnAddRow.className = "btn btn-sm btn-add-row";
    btnAddRow.innerText = "+ Add Platform Link";
    btnAddRow.addEventListener("click", () => addSubrow(channelName));
    card.appendChild(btnAddRow);
    
    channelsGrid.appendChild(card);
  }
}

// --- CHANNEL MODIFICATION ---

function addNewChannel() {
  let index = 1;
  let newName = `New Channel ${index}`;
  while (localChannels[newName]) {
    index++;
    newName = `New Channel ${index}`;
  }
  
  localChannels[newName] = [];
  localDefaultUrls[newName] = {};
  renderChannels();
  saveConfig(false);
}

function renameChannel(oldName, newName) {
  if (newName === "" || oldName === newName) {
    renderChannels();
    return;
  }
  if (localChannels[newName]) {
    alert("A channel with that name already exists.");
    renderChannels();
    return;
  }
  
  // Re-key objects
  localChannels[newName] = localChannels[oldName];
  delete localChannels[oldName];
  
  if (localDefaultUrls[oldName]) {
    localDefaultUrls[newName] = localDefaultUrls[oldName];
    delete localDefaultUrls[oldName];
  }
  renderChannels();
  saveConfig(false);
}

function deleteChannel(channelName) {
  if (confirm(`Are you sure you want to delete channel "${channelName}"?`)) {
    delete localChannels[channelName];
    delete localDefaultUrls[channelName];
    renderChannels();
    saveConfig(false);
  }
}

function addSubrow(channelName) {
  localChannels[channelName].push({ platform: "youtube", url: "", type: "online" });
  renderChannels();
  saveConfig(false);
}

function removeSubrow(channelName, rowIndex) {
  localChannels[channelName].splice(rowIndex, 1);
  renderChannels();
  saveConfig(false);
}

// --- MODAL DIALOGS ---

function openUrlModal(channelName, rowIndex) {
  activeModalTarget = { channelName, rowIndex };
  const rowData = localChannels[channelName][rowIndex];
  
  modalTitle.innerText = `Link Config for ${channelName} (${rowData.platform.toUpperCase()})`;
  modalUrlInput.value = rowData.url || "";
  
  // Prepare scanning section
  const defaults = localDefaultUrls[channelName] || {};
  const defaultUrl = defaults[rowData.platform];
  
  const scanDefaultUrlInfo = document.getElementById("scan-default-url-info");
  const scanControlsGroup = document.getElementById("scan-controls-group");
  const scanNoDefaultUrl = document.getElementById("scan-no-default-url");
  const scanLoading = document.getElementById("scan-loading");
  const scanResultsContainer = document.getElementById("scan-results-container");
  const scanResultsList = document.getElementById("scan-results-list");
  const keywordInput = document.getElementById("modal-keyword-input");
  
  // Reset scan UI state
  keywordInput.value = "";
  scanLoading.classList.add("hidden");
  scanResultsContainer.classList.add("hidden");
  scanResultsList.innerHTML = "";
  
  if (defaultUrl) {
    scanDefaultUrlInfo.innerText = `Default URL: ${defaultUrl}`;
    scanDefaultUrlInfo.classList.remove("hidden");
    scanControlsGroup.classList.remove("hidden");
    scanNoDefaultUrl.classList.add("hidden");
  } else {
    scanDefaultUrlInfo.classList.add("hidden");
    scanControlsGroup.classList.add("hidden");
    scanNoDefaultUrl.classList.remove("hidden");
  }
  
  urlModal.classList.add("open");
  modalUrlInput.focus();
}

function closeModal() {
  urlModal.classList.remove("open");
  activeModalTarget = null;
}

function saveModalUrl() {
  if (activeModalTarget) {
    const { channelName, rowIndex } = activeModalTarget;
    const rowData = localChannels[channelName][rowIndex];
    const urlVal = modalUrlInput.value.trim();
    
    if (urlVal !== "") {
      const platform = rowData.platform;
      if (platform === "youtube" && !urlVal.includes("youtube.com") && !urlVal.includes("youtu.be")) {
        alert("Invalid YouTube URL! Please enter a link containing 'youtube.com' or 'youtu.be'.");
        modalUrlInput.focus();
        return;
      }
      if (platform === "facebook" && !urlVal.includes("facebook.com")) {
        alert("Invalid Facebook URL! Please enter a link containing 'facebook.com'.");
        modalUrlInput.focus();
        return;
      }
      if (platform === "tiktok" && !urlVal.includes("tiktok.com")) {
        alert("Invalid TikTok URL! Please enter a link containing 'tiktok.com'.");
        modalUrlInput.focus();
        return;
      }
      if (platform === "x" && !urlVal.includes("x.com") && !urlVal.includes("twitter.com")) {
        alert("Invalid X (Twitter) URL! Please enter a link containing 'x.com' or 'twitter.com'.");
        modalUrlInput.focus();
        return;
      }

      // Check for duplicate URLs across all channels and platforms
      let isDuplicate = false;
      let duplicateChannel = "";
      let duplicatePlatform = "";
      
      for (const chName in localChannels) {
        const chRows = localChannels[chName];
        for (let rIdx = 0; rIdx < chRows.length; rIdx++) {
          // Skip the current active row being edited
          if (chName === channelName && rIdx === rowIndex) {
            continue;
          }
          if (chRows[rIdx].url && chRows[rIdx].url.trim() === urlVal) {
            isDuplicate = true;
            duplicateChannel = chName;
            duplicatePlatform = chRows[rIdx].platform;
            break;
          }
        }
        if (isDuplicate) break;
      }
      
      if (isDuplicate) {
        alert(`Duplicate URL! This URL is already configured under channel "${duplicateChannel}" for platform "${duplicatePlatform.toUpperCase()}".`);
        modalUrlInput.focus();
        return;
      }
    }
    
    localChannels[channelName][rowIndex].url = urlVal;
    closeModal();
    renderChannels();
    saveConfig(false);
  }
}

// --- URL DUMP MODAL FUNCTIONS ---
function openUrlDump(channelName) {
  activeDumpChannel = channelName;
  document.getElementById("url-dump-modal-title").innerText = `Dump URLs for ${channelName}`;
  document.getElementById("url-dump-textarea").value = "";
  document.getElementById("url-dump-error").style.display = "none";
  document.getElementById("url-dump-error").innerText = "";
  document.getElementById("url-dump-success-msg").style.display = "none";
  document.getElementById("url-dump-success-msg").innerText = "";
  urlDumpModal.classList.add("open");
}

function closeUrlDumpModal() {
  urlDumpModal.classList.remove("open");
  activeDumpChannel = null;
}

function detectPlatform(url) {
  const lowercaseUrl = url.toLowerCase();
  if (lowercaseUrl.includes("youtube.com") || lowercaseUrl.includes("youtu.be")) {
    return "youtube";
  } else if (lowercaseUrl.includes("facebook.com") || lowercaseUrl.includes("fb.watch")) {
    return "facebook";
  } else if (lowercaseUrl.includes("tiktok.com")) {
    return "tiktok";
  } else if (lowercaseUrl.includes("twitter.com") || lowercaseUrl.includes("x.com")) {
    return "x";
  }
  return null;
}

function importDumpedUrls() {
  if (!activeDumpChannel) return;
  const textarea = document.getElementById("url-dump-textarea");
  const errorDiv = document.getElementById("url-dump-error");
  const successDiv = document.getElementById("url-dump-success-msg");
  
  errorDiv.style.display = "none";
  errorDiv.innerText = "";
  successDiv.style.display = "none";
  successDiv.innerText = "";
  
  const text = textarea.value.trim();
  if (text === "") {
    errorDiv.innerText = "Please paste at least one URL.";
    errorDiv.style.display = "block";
    return;
  }
  
  // Split by commas, whitespaces (spaces/tabs/newlines)
  const tokens = text.split(/[\s,]+/);
  const urls = tokens.map(u => u.trim()).filter(u => u.length > 0);
  
  if (urls.length === 0) {
    errorDiv.innerText = "No valid URLs found.";
    errorDiv.style.display = "block";
    return;
  }
  
  const skippedInvalid = [];
  const skippedDuplicate = [];
  let addedCount = 0;
  
  urls.forEach(urlVal => {
    const platform = detectPlatform(urlVal);
    if (!platform) {
      skippedInvalid.push(urlVal);
      return;
    }
    
    // Check for duplicate URLs across all channels and platforms
    let isDuplicate = false;
    let duplicateChannel = "";
    let duplicatePlatform = "";
    
    for (const chName in localChannels) {
      const chRows = localChannels[chName];
      for (let rIdx = 0; rIdx < chRows.length; rIdx++) {
        if (chRows[rIdx].url && chRows[rIdx].url.trim() === urlVal) {
          isDuplicate = true;
          duplicateChannel = chName;
          duplicatePlatform = chRows[rIdx].platform;
          break;
        }
      }
      if (isDuplicate) break;
    }
    
    if (isDuplicate) {
      skippedDuplicate.push(`${urlVal} (configured under channel "${duplicateChannel}" - ${duplicatePlatform.toUpperCase()})`);
      return;
    }
    
    // We can add the URL.
    // Check if there is an empty platform row for this channel
    const chRows = localChannels[activeDumpChannel];
    let populated = false;
    for (let rIdx = 0; rIdx < chRows.length; rIdx++) {
      if (chRows[rIdx].platform === platform && (!chRows[rIdx].url || chRows[rIdx].url.trim() === "")) {
        chRows[rIdx].url = urlVal;
        populated = true;
        break;
      }
    }
    
    if (!populated) {
      // Create a new platform row
      chRows.push({
        platform: platform,
        type: "online",
        url: urlVal
      });
    }
    
    addedCount++;
  });
  
  if (addedCount > 0) {
    renderChannels();
    saveConfig(false);
  }
  
  // Build feedback
  let feedbackErrors = [];
  if (skippedInvalid.length > 0) {
    feedbackErrors.push(`Could not identify platform for ${skippedInvalid.length} URL(s):\n` + skippedInvalid.join("\n"));
  }
  if (skippedDuplicate.length > 0) {
    feedbackErrors.push(`Skipped ${skippedDuplicate.length} duplicate URL(s):\n` + skippedDuplicate.join("\n"));
  }
  
  if (feedbackErrors.length > 0) {
    errorDiv.innerText = feedbackErrors.join("\n\n");
    errorDiv.style.display = "block";
    
    if (addedCount > 0) {
      successDiv.innerText = `Successfully imported ${addedCount} URL(s).`;
      successDiv.style.display = "block";
    }
  } else {
    // All succeeded, close modal
    closeUrlDumpModal();
  }
}

// --- CHANNEL SETTINGS MODAL FUNCTIONS ---
function openChannelSettings(channelName) {
  activeSettingsChannel = channelName;
  settingsTitle.innerText = `Channel Settings for ${channelName}`;
  
  const defaults = localDefaultUrls[channelName] || {};
  inputDefaultYoutube.value = defaults.youtube || "";
  inputDefaultFacebook.value = defaults.facebook || "";
  inputDefaultTiktok.value = defaults.tiktok || "";
  inputDefaultX.value = defaults.x || "";
  
  channelSettingsModal.classList.add("open");
}

function closeChannelSettingsModal() {
  channelSettingsModal.classList.remove("open");
  activeSettingsChannel = null;
}

function saveChannelSettings() {
  if (activeSettingsChannel) {
    const ytVal = inputDefaultYoutube.value.trim();
    const fbVal = inputDefaultFacebook.value.trim();
    const ttVal = inputDefaultTiktok.value.trim();
    const xVal = inputDefaultX.value.trim();
    
    if (ytVal !== "" && !ytVal.includes("youtube.com") && !ytVal.includes("youtu.be")) {
      alert("Invalid YouTube URL! Please enter a link containing 'youtube.com' or 'youtu.be'.");
      inputDefaultYoutube.focus();
      return;
    }
    if (fbVal !== "") {
      if (!fbVal.includes("facebook.com/watch/")) {
        alert("Invalid Facebook default URL! We now use the optimal watch layout. Please enter a link using the watch format (e.g. 'https://www.facebook.com/watch/thairath/').");
        inputDefaultFacebook.focus();
        return;
      }
    }
    if (ttVal !== "" && !ttVal.includes("tiktok.com")) {
      alert("Invalid TikTok URL! Please enter a link containing 'tiktok.com'.");
      inputDefaultTiktok.focus();
      return;
    }
    if (xVal !== "" && !xVal.includes("x.com") && !xVal.includes("twitter.com")) {
      alert("Invalid X (Twitter) URL! Please enter a link containing 'x.com' or 'twitter.com'.");
      inputDefaultX.focus();
      return;
    }
    
    if (!localDefaultUrls[activeSettingsChannel]) {
      localDefaultUrls[activeSettingsChannel] = {};
    }
    const defaults = localDefaultUrls[activeSettingsChannel];
    defaults.youtube = ytVal;
    defaults.facebook = fbVal;
    defaults.tiktok = ttVal;
    defaults.x = xVal;
    
    // Clean up empty default URLs
    for (const key in defaults) {
      if (defaults[key] === "") {
        delete defaults[key];
      }
    }
    
    saveConfig(true);
    closeChannelSettingsModal();
  }
}

// --- LIVE SCANNER ---
function runLiveScan() {
  if (!activeModalTarget) return;
  const { channelName, rowIndex } = activeModalTarget;
  const rowData = localChannels[channelName][rowIndex];
  const keywordInput = document.getElementById("modal-keyword-input");
  const keyword = keywordInput.value.trim();
  
  const scanLoading = document.getElementById("scan-loading");
  const scanResultsContainer = document.getElementById("scan-results-container");
  const scanResultsList = document.getElementById("scan-results-list");
  const btnScanLive = document.getElementById("btn-scan-live");
  
  // UI Loading State
  scanLoading.classList.remove("hidden");
  scanResultsContainer.classList.add("hidden");
  scanResultsList.innerHTML = "";
  btnScanLive.disabled = true;
  
  fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      channelName,
      platform: rowData.platform,
      keyword
    })
  })
    .then(res => res.json())
    .then(data => {
      btnScanLive.disabled = false;
      scanLoading.classList.add("hidden");
      
      if (data.success) {
        const matches = data.matches || [];
        if (matches.length === 0) {
          scanResultsList.innerHTML = '<div class="scan-info-text">No active live streams found matching keyword.</div>';
          scanResultsContainer.classList.remove("hidden");
          return;
        }
        
        matches.forEach(match => {
          const row = document.createElement("div");
          row.className = "scan-result-row";
          
          const info = document.createElement("div");
          info.className = "scan-result-info";
          
          const title = document.createElement("div");
          title.className = "scan-result-title";
          title.innerText = match.title || "No Title";
          title.title = match.title;
          
          const meta = document.createElement("div");
          meta.className = "scan-result-meta";
          meta.innerText = `${match.viewers || 0} viewers`;
          
          info.appendChild(title);
          info.appendChild(meta);
          
          const actions = document.createElement("div");
          actions.className = "scan-result-actions";
          
          const link = document.createElement("a");
          link.className = "scan-result-link";
          link.href = match.url;
          link.target = "_blank";
          link.innerText = "Open 🔗";
          link.title = match.url;
          
          const btnConfirm = document.createElement("button");
          btnConfirm.className = "btn btn-sm btn-primary";
          btnConfirm.innerText = "Confirm";
          btnConfirm.addEventListener("click", () => {
            modalUrlInput.value = match.url;
          });
          
          const btnReject = document.createElement("button");
          btnReject.className = "btn btn-sm btn-danger";
          btnReject.innerText = "Reject";
          btnReject.addEventListener("click", () => {
            row.remove();
            if (scanResultsList.children.length === 0) {
              scanResultsList.innerHTML = '<div class="scan-info-text">No active live streams found matching keyword.</div>';
            }
          });
          
          actions.appendChild(link);
          actions.appendChild(btnConfirm);
          actions.appendChild(btnReject);
          
          row.appendChild(info);
          row.appendChild(actions);
          scanResultsList.appendChild(row);
        });
        
        scanResultsContainer.classList.remove("hidden");
      } else {
        alert(`Scan failed: ${data.error}`);
      }
    })
    .catch(err => {
      btnScanLive.disabled = false;
      scanLoading.classList.add("hidden");
      alert(`Scan network error: ${err.message}`);
    });
};

// --- HELPERS ---
function appendLogLine(line) {
  const isScrollAtBottom = terminalOutput.scrollHeight - terminalOutput.clientHeight <= terminalOutput.scrollTop + 1;
  const span = document.createElement("span");
  span.innerText = line;
  terminalOutput.appendChild(span);
  if (isScrollAtBottom) {
    terminalOutput.scrollTop = terminalOutput.scrollHeight;
  }
}

function showTerminalError(msg) {
  const span = document.createElement("span");
  span.style.color = "var(--danger)";
  span.innerText = `[Error] ${msg}\n`;
  terminalOutput.appendChild(span);
  terminalOutput.scrollTop = terminalOutput.scrollHeight;
}

// --- RUN CHANNELS MODAL CONTROLS ---
function openRunChannelsModal() {
  runChannelsList.innerHTML = "";
  
  const channelNames = Object.keys(localChannels);
  
  if (channelNames.length === 0) {
    runChannelsList.innerHTML = '<div class="scan-info-text" style="color: var(--warning); margin-bottom: 0;">⚠️ No channels configured yet. Add a news channel first.</div>';
  } else {
    channelNames.forEach(name => {
      const channelGroup = document.createElement("div");
      channelGroup.className = "channel-run-group";
      
      const channelHeaderRow = document.createElement("div");
      channelHeaderRow.className = "channel-header-row";
      
      const channelLabel = document.createElement("label");
      channelLabel.className = "checkbox-label";
      
      const channelChk = document.createElement("input");
      channelChk.type = "checkbox";
      channelChk.className = "chk-channel-parent";
      channelChk.value = name;
      channelChk.checked = true; // Default is select all
      
      const channelSpan = document.createElement("span");
      channelSpan.innerHTML = `<strong>${name}</strong>`;
      
      channelLabel.appendChild(channelChk);
      channelLabel.appendChild(channelSpan);
      channelHeaderRow.appendChild(channelLabel);
      channelGroup.appendChild(channelHeaderRow);
      
      // Get unique configured platforms
      const rows = localChannels[name] || [];
      const platforms = Array.from(new Set(rows.map(r => r.platform).filter(Boolean)));
      
      const platformsList = document.createElement("div");
      platformsList.className = "channel-platforms-list";
      
      if (platforms.length === 0) {
        const noPlatforms = document.createElement("div");
        noPlatforms.className = "scan-info-text";
        noPlatforms.style.marginLeft = "26px";
        noPlatforms.style.marginTop = "2px";
        noPlatforms.style.marginBottom = "2px";
        noPlatforms.style.color = "var(--text-muted)";
        noPlatforms.innerText = "No platforms configured";
        channelGroup.appendChild(noPlatforms);
      } else {
        platforms.forEach(platform => {
          const platformItem = document.createElement("div");
          platformItem.className = "platform-run-item";
          
          const platformLabel = document.createElement("label");
          platformLabel.className = "checkbox-label";
          
          const platformChk = document.createElement("input");
          platformChk.type = "checkbox";
          platformChk.className = "chk-platform-item";
          platformChk.dataset.channel = name;
          platformChk.value = platform;
          platformChk.checked = true;
          
          platformChk.addEventListener("change", () => {
            const childChks = platformsList.querySelectorAll(".chk-platform-item");
            const anyChecked = Array.from(childChks).some(c => c.checked);
            channelChk.checked = anyChecked;
          });
          
          const platformSpan = document.createElement("span");
          platformSpan.innerText = platform.toUpperCase();
          
          platformLabel.appendChild(platformChk);
          platformLabel.appendChild(platformSpan);
          platformItem.appendChild(platformLabel);
          platformsList.appendChild(platformItem);
        });
        
        channelChk.addEventListener("change", (e) => {
          const isChecked = e.target.checked;
          const childChks = platformsList.querySelectorAll(".chk-platform-item");
          childChks.forEach(c => c.checked = isChecked);
        });
        
        channelGroup.appendChild(platformsList);
      }
      
      runChannelsList.appendChild(channelGroup);
    });
  }
  
  runChannelsModal.classList.add("open");
}

function closeRunChannelsModal() {
  runChannelsModal.classList.remove("open");
}

function selectAllChannels() {
  const checkboxes = runChannelsList.querySelectorAll("input[type='checkbox']");
  checkboxes.forEach(chk => chk.checked = true);
}

function deselectAllChannels() {
  const checkboxes = runChannelsList.querySelectorAll("input[type='checkbox']");
  checkboxes.forEach(chk => chk.checked = false);
}

// --- REORDER CHANNELS CONTROLS ---
function openReorderModal() {
  if (!reorderList) return;
  reorderList.innerHTML = "";
  
  const channelNames = Object.keys(localChannels);
  if (channelNames.length === 0) {
    reorderList.innerHTML = '<div class="scan-info-text" style="color: var(--warning); margin-bottom: 0;">⚠️ No channels configured yet. Add a news channel first.</div>';
  } else {
    channelNames.forEach(name => {
      const item = document.createElement("div");
      item.className = "reorder-item";
      item.setAttribute("draggable", "true");
      item.setAttribute("data-name", name);
      
      const handle = document.createElement("span");
      handle.className = "drag-handle";
      
      const text = document.createElement("span");
      text.innerText = name;
      
      item.appendChild(handle);
      item.appendChild(text);
      
      // Drag events
      item.addEventListener("dragstart", () => {
        item.classList.add("dragging");
      });
      
      item.addEventListener("dragend", () => {
        item.classList.remove("dragging");
      });
      
      reorderList.appendChild(item);
    });
  }
  
  if (reorderModal) reorderModal.classList.add("open");
}

function closeReorderModal() {
  if (reorderModal) reorderModal.classList.remove("open");
}

function handleReorderDragOver(e) {
  e.preventDefault();
  const draggingItem = document.querySelector(".reorder-item.dragging");
  if (!draggingItem) return;
  
  const afterElement = getDragAfterElement(reorderList, e.clientY);
  if (afterElement == null) {
    reorderList.appendChild(draggingItem);
  } else {
    reorderList.insertBefore(draggingItem, afterElement);
  }
}

function getDragAfterElement(container, y) {
  const draggableElements = [...container.querySelectorAll(".reorder-item:not(.dragging)")];
  return draggableElements.reduce((closest, child) => {
    const box = child.getBoundingClientRect();
    const offset = y - box.top - box.height / 2;
    if (offset < 0 && offset > closest.offset) {
      return { offset: offset, element: child };
    } else {
      return closest;
    }
  }, { offset: Number.NEGATIVE_INFINITY }).element;
}

function saveReorderedChannels() {
  if (!reorderList) return;
  const items = [...reorderList.querySelectorAll(".reorder-item")];
  const newOrder = items.map(item => item.getAttribute("data-name"));
  
  if (newOrder.length > 0) {
    // Reorder localChannels
    const reorderedChannels = {};
    newOrder.forEach(name => {
      if (localChannels.hasOwnProperty(name)) {
        reorderedChannels[name] = localChannels[name];
      }
    });
    localChannels = reorderedChannels;
    
    // Reorder localDefaultUrls
    const reorderedDefaultUrls = {};
    newOrder.forEach(name => {
      if (localDefaultUrls.hasOwnProperty(name)) {
        reorderedDefaultUrls[name] = localDefaultUrls[name];
      }
    });
    localDefaultUrls = reorderedDefaultUrls;
    
    renderChannels();
    saveConfig(false);
  }
  closeReorderModal();
}
