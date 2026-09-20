const { app, BrowserWindow, shell, ipcMain } = require("electron");
const path = require("path");
const http = require("http");
const fs = require("fs");

let keytar;
try {
  keytar = require("keytar");
} catch {
  // keytar needs native rebuild after npm install — warn but don't crash
  console.warn("[electron] keytar not available, API key storage disabled");
}

const NEXT_PORT = 3000;
const isDev = process.env.NODE_ENV === "development";

// Where a packaged .exe points. In development everything runs locally on
// localhost:3000; a built app is a thin client that loads the server instead
// and starts nothing of its own. The default is the production domain;
// YOUR_OWN_SERVER_URL overrides it (a different server, or a temporary host
// while the domain is being sorted out).
const REMOTE_URL = process.env.YOUR_OWN_SERVER_URL || "https://victoraihome.com";

// The address the window loads: local dev server, or the remote server.
function windowURL() {
  return isDev ? `http://localhost:${NEXT_PORT}` : REMOTE_URL;
}

// Some Windows setups crash the Electron GPU process on startup.
// Falling back to software rendering is safer than failing the app boot.
app.disableHardwareAcceleration();

const KEYTAR_SERVICE           = "your-own-app";
const KEYTAR_ACCOUNT_APIKEY    = "openrouter-api-key";
const KEYTAR_ACCOUNT_MODEL     = "selected-model";
const KEYTAR_ACCOUNT_TEMP      = "temperature";
const KEYTAR_ACCOUNT_TOPP      = "top-p";
const KEYTAR_ACCOUNT_HISTORY   = "chat-history-pairs";
const KEYTAR_ACCOUNT_CUTOFF    = "memory-cutoff-days";

let mainWindow = null;

// ── Keychain IPC handlers ─────────────────────────────────────────────────────

ipcMain.handle("save-api-key", async (_event, key) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_APIKEY, key);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-api-key", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_APIKEY);
  } catch {
    return null;
  }
});

ipcMain.handle("save-model", async (_event, model) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_MODEL, model);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-model", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_MODEL);
  } catch {
    return null;
  }
});

ipcMain.handle("save-temperature", async (_event, val) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_TEMP, String(val));
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-temperature", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_TEMP);
  } catch {
    return null;
  }
});

ipcMain.handle("save-top-p", async (_event, val) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_TOPP, String(val));
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-top-p", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_TOPP);
  } catch {
    return null;
  }
});

ipcMain.handle("save-history-pairs", async (_event, value) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_HISTORY, String(value));
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-history-pairs", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_HISTORY);
  } catch {
    return null;
  }
});

ipcMain.handle("save-memory-cutoff-days", async (_event, value) => {
  if (!keytar) return { ok: false, error: "keytar unavailable" };
  try {
    await keytar.setPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_CUTOFF, String(value));
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-memory-cutoff-days", async () => {
  if (!keytar) return null;
  try {
    return await keytar.getPassword(KEYTAR_SERVICE, KEYTAR_ACCOUNT_CUTOFF);
  } catch {
    return null;
  }
});

// soul.md lives in app userData directory (persists across updates, not in source)
function soulPath() {
  return path.join(app.getPath("userData"), "soul.md");
}

ipcMain.handle("save-soul", async (_event, text) => {
  try {
    fs.writeFileSync(soulPath(), text, "utf-8");
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle("get-soul", async () => {
  try {
    const p = soulPath();
    if (!fs.existsSync(p)) return null;
    return fs.readFileSync(p, "utf-8");
  } catch {
    return null;
  }
});

// ── Backend auth token ────────────────────────────────────────────────────────

// The auth token lives on the server now, not on this machine. In development
// the backend runs from this repo and writes it to data/auth_token.txt, so the
// dev app can still auto-configure by reading that file. A packaged app is a
// thin client to a remote server — there is no local token file, so this
// returns null and the user pastes the token once in Settings, exactly as the
// phone and web clients do.
function devAuthTokenPath() {
  return path.join(__dirname, "..", "..", "data", "auth_token.txt");
}

ipcMain.handle("get-backend-auth-token", async () => {
  if (!isDev) return null;
  try {
    const p = devAuthTokenPath();
    if (!fs.existsSync(p)) return null;
    return fs.readFileSync(p, "utf-8").trim() || null;
  } catch {
    return null;
  }
});

// ── Wait until Next.js server is ready ───────────────────────────────────────

function waitForServer(port, retries = 60, interval = 500) {
  return new Promise((resolve, reject) => {
    let attempts = 0;
    const check = () => {
      const req = http.get(`http://localhost:${port}/`, (res) => {
        res.resume(); // consume response so socket closes
        resolve();
      });
      req.setTimeout(1000, () => { req.destroy(); });
      req.on("error", () => {
        if (++attempts >= retries) {
          reject(new Error(`Server on port ${port} did not start in time`));
        } else {
          setTimeout(check, interval);
        }
      });
      req.end();
    };
    check();
  });
}

// ── Create the window ─────────────────────────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#000000",
    titleBarStyle: "hiddenInset",
    frame: process.platform !== "darwin",
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, "preload.js"),
    },
  });

  mainWindow.loadURL(windowURL());

  mainWindow.once("ready-to-show", () => mainWindow.show());

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => { mainWindow = null; });
}

// ── App lifecycle ─────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  // In development, wait for the local Next.js dev server before opening the
  // window; the backend is started separately by the concurrently script. A
  // packaged app loads the remote server directly — nothing local to wait for,
  // and the page itself surfaces a network error if the server is unreachable.
  if (isDev) {
    try {
      await waitForServer(NEXT_PORT);
    } catch (err) {
      console.error("[electron] Next.js not ready:", err.message);
    }
  }

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
