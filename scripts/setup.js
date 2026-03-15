#!/usr/bin/env node
/**
 * One-time setup script — runs before `npm run electron:dev` or on first launch.
 *
 * What it does (idempotent — safe to run multiple times):
 *   1. Detect OS & find / install PostgreSQL if missing
 *   2. Ensure the `your_own` database exists with password 1234
 *   3. Write .env from .env.example if .env is absent
 *   4. Install frontend dependencies (npm install in frontend)
 *   5. Install Python dependencies (pip install -r requirements.txt)
 *   6. Run `alembic upgrade head`
 *
 * All config constants live at the top of this file.
 */

"use strict";

const { execSync, spawnSync } = require("child_process");
const fs        = require("fs");
const path      = require("path");
const os        = require("os");
const readline  = require("readline");

// ── Config ────────────────────────────────────────────────────────────────────

const DB_NAME = "your_own";
const DB_USER = "postgres";
const DB_PORT = 5432;

// DB_PASSWORD is resolved at runtime — either from .env or by asking once
let DB_PASSWORD = "";
let DB_URL = "";

const ROOT = path.resolve(__dirname, "..");   // workspace root
const FRONTEND = path.join(ROOT, "frontend");
const VENV_PYTHON = os.platform() === "win32"
  ? path.join(ROOT, ".venv", "Scripts", "python.exe")
  : path.join(ROOT, ".venv", "bin", "python");

// ── Helpers ───────────────────────────────────────────────────────────────────

function log(msg)  { console.log(`\x1b[36m[setup]\x1b[0m ${msg}`); }
function ok(msg)   { console.log(`\x1b[32m[setup ✓]\x1b[0m ${msg}`); }
function warn(msg) { console.log(`\x1b[33m[setup !]\x1b[0m ${msg}`); }
function err(msg)  { console.error(`\x1b[31m[setup ✗]\x1b[0m ${msg}`); }

function run(cmd, opts = {}) {
  return spawnSync(cmd, { shell: true, encoding: "utf8", cwd: ROOT, ...opts });
}

function runIn(dir, cmd, opts = {}) {
  return spawnSync(cmd, { shell: true, encoding: "utf8", cwd: dir, ...opts });
}

function which(bin) {
  // On Windows, also check known PostgreSQL install paths directly
  if (os.platform() === "win32" && (bin === "psql" || bin === "pg_isready")) {
    const pgBin = findPostgresBinWinSafe();
    if (pgBin) {
      const full = path.join(pgBin, `${bin}.exe`);
      if (fs.existsSync(full)) return full;
    }
  }
  const r = run(`${os.platform() === "win32" ? "where" : "which"} ${bin}`);
  return r.status === 0 ? r.stdout.trim().split("\n")[0] : null;
}

/** Safe version usable before findPostgresBinWin is defined. */
function findPostgresBinWinSafe() {
  const candidates = [
    "C:\\Program Files\\PostgreSQL\\17\\bin",
    "C:\\Program Files\\PostgreSQL\\16\\bin",
    "C:\\Program Files\\PostgreSQL\\15\\bin",
    "C:\\Program Files\\PostgreSQL\\14\\bin",
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, "psql.exe"))) return c;
  }
  return null;
}

// ── Step 1: Ensure PostgreSQL is installed ────────────────────────────────────

function isAdminWindows() {
  const r = run('powershell -Command "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"');
  return r.stdout.trim().toLowerCase() === "true";
}

function installChocolatey() {
  log("Installing Chocolatey…");

  if (!isAdminWindows()) {
    // Re-launch this entire setup script as Administrator and wait for it to finish
    warn("Administrator rights required — requesting elevation via UAC…");
    const scriptPath = path.resolve(__filename);
    const elevateCmd = [
      "powershell",
      "-NoProfile",
      "-ExecutionPolicy", "Bypass",
      "-Command",
      `Start-Process node -ArgumentList '"${scriptPath}"' -Verb RunAs -Wait`,
    ].join(" ");
    const r = run(elevateCmd);
    if (r.status !== 0) {
      err([
        "UAC elevation failed or was denied.",
        "Please re-run npm run electron:dev from an Administrator terminal.",
      ].join("\n  "));
      process.exit(1);
    }
    // After the elevated process finishes setup, exit this non-admin process cleanly
    // The elevated run already did everything including choco + pg install
    process.exit(0);
  }

  const cmd = [
    "powershell",
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-Command",
    [
      "Set-ExecutionPolicy Bypass -Scope Process -Force;",
      "[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072;",
      "iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))",
    ].join(" "),
  ].join(" ");

  const r = run(cmd, { timeout: 120_000 });
  if (r.status !== 0) {
    err([
      "Failed to install Chocolatey.",
      "stderr: " + r.stderr,
    ].join("\n  "));
    process.exit(1);
  }
  ok("Chocolatey installed");

  // Update PATH for current process so choco.exe is immediately available
  const chocoPath = "C:\\ProgramData\\chocolatey\\bin";
  if (fs.existsSync(chocoPath)) {
    process.env.PATH = `${chocoPath};${process.env.PATH}`;
  }
}

function installHomebrew() {
  log("Installing Homebrew…");
  const cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"';
  execSync(cmd, { stdio: "inherit", shell: true });
  ok("Homebrew installed");
}

function ensurePostgres() {
  log("Checking PostgreSQL…");

  const haspsql = which("psql");
  if (haspsql) {
    ok(`psql found: ${haspsql}`);
    return;
  }

  warn("psql not found — attempting automatic install…");
  const platform = os.platform();

  if (platform === "darwin") {
    if (!which("brew")) {
      warn("Homebrew not found — installing it first…");
      installHomebrew();
    }
    log("brew install postgresql@16 …");
    execSync("brew install postgresql@16", { stdio: "inherit" });
    execSync("brew services start postgresql@16", { stdio: "inherit" });
    // Add to PATH for the current process
    const brewPrefix = run("brew --prefix").stdout.trim();
    process.env.PATH = `${brewPrefix}/opt/postgresql@16/bin:${process.env.PATH}`;
    execSync(`echo 'export PATH="${brewPrefix}/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc`, { shell: true });
    ok("PostgreSQL installed via Homebrew");

  } else if (platform === "linux") {
    log("sudo apt-get install -y postgresql postgresql-contrib …");
    execSync("sudo apt-get update -qq && sudo apt-get install -y postgresql postgresql-contrib", { stdio: "inherit" });
    execSync("sudo service postgresql start", { stdio: "inherit", shell: true });
    ok("PostgreSQL installed via apt-get");

  } else if (platform === "win32") {
    if (!which("choco")) {
      warn("Chocolatey not found — installing it first…");
      installChocolatey();
    }

    log("choco install postgresql16 …");
    // --params sets the superuser password so we don't have to ALTER USER later
    const r = run(
      `choco install postgresql16 --params "/Password:${DB_PASSWORD}" -y`,
      { timeout: 300_000 }   // 5 min — installer is slow
    );
    if (r.status !== 0) {
      err(`choco install postgresql16 failed:\n${r.stderr}`);
      process.exit(1);
    }

    // Refresh PATH — choco adds pg bin dir to system PATH but not current process
    const pgBin = findPostgresBinWinSafe();
    if (pgBin) process.env.PATH = `${pgBin};${process.env.PATH}`;
    ok("PostgreSQL installed via Chocolatey");

  } else {
    err(`Unknown platform: ${platform}. Install PostgreSQL manually.`);
    process.exit(1);
  }
}


// ── Step 1b: Ensure PostgreSQL service is running ────────────────────────────

const PG_SERVICE_NAMES = ["postgresql-x64-17", "postgresql-x64-16", "postgresql-x64-15", "postgresql-x64-14", "postgresql"];

function detectPgServiceName() {
  for (const svc of PG_SERVICE_NAMES) {
    const status = run(`sc query "${svc}"`);
    if (status.stdout && (status.stdout.includes("RUNNING") || status.stdout.includes("STOPPED"))) {
      return svc;
    }
  }
  return null;
}

function ensurePostgresRunning() {
  const platform = os.platform();

  if (platform === "win32") {
    for (const svc of PG_SERVICE_NAMES) {
      const status = run(`sc query "${svc}"`);
      if (status.stdout && status.stdout.includes("RUNNING")) {
        ok(`PostgreSQL service "${svc}" is running`);
        return;
      }
      if (status.stdout && (status.stdout.includes("STOPPED") || status.stdout.includes("START_PENDING"))) {
        log(`Starting PostgreSQL service "${svc}"…`);
        const start = run(`net start "${svc}"`);
        if (start.status === 0) {
          ok(`PostgreSQL service "${svc}" started`);
          return;
        }
      }
    }
    warn("Could not auto-start PostgreSQL service — trying to connect anyway");

  } else if (platform === "darwin") {
    run("brew services start postgresql@16");

  } else if (platform === "linux") {
    run("sudo service postgresql start");
  }
}

function restartPostgres() {
  // On Windows this is handled inside the elevated ps1 (Restart-Service).
  // On macOS/Linux no elevation needed.
  const platform = os.platform();
  if (platform === "darwin") {
    run("brew services restart postgresql@17 || brew services restart postgresql@16");
  } else if (platform === "linux") {
    run("sudo service postgresql restart");
  }
}

// ── Step 2: Ensure database exists ───────────────────────────────────────────

function psql(sql, dbName = "postgres") {
  // PGPASSWORD is set in the environment so psql never prompts interactively
  const pgEnv = { ...process.env, PGPASSWORD: DB_PASSWORD };
  return run(
    `psql -U ${DB_USER} -h localhost -p ${DB_PORT} -d ${dbName} -c "${sql}"`,
    { env: pgEnv }
  );
}

function psqlList() {
  const pgEnv = { ...process.env, PGPASSWORD: DB_PASSWORD };
  return run(
    `psql -U ${DB_USER} -h localhost -p ${DB_PORT} -lqt`,
    { env: pgEnv }
  );
}

function ensureDatabase() {
  log("Ensuring PostgreSQL service is running…");
  ensurePostgresRunning();

  log(`Checking database "${DB_NAME}"…`);

  // Check if DB already exists
  const list = psqlList();
  if (list.stdout && list.stdout.includes(DB_NAME)) {
    ok(`Database "${DB_NAME}" already exists`);
    return;
  }

  log(`Creating database "${DB_NAME}"…`);
  const create = psql(`CREATE DATABASE ${DB_NAME};`);
  if (create.status !== 0) {
    err(`Failed to create database:\n${create.stderr}`);
    process.exit(1);
  }
  ok(`Database "${DB_NAME}" created`);
}

// ── Step 3: Write .env if absent ─────────────────────────────────────────────

function ensureEnv() {
  const envPath     = path.join(ROOT, ".env");
  const examplePath = path.join(ROOT, ".env.example");

  if (fs.existsSync(envPath)) {
    ok(".env already exists — skipping");

    // Ensure DATABASE_URL in existing .env matches our defaults
    let content = fs.readFileSync(envPath, "utf8");
    if (!content.includes("DATABASE_URL")) {
      content += `\nDATABASE_URL=${DB_URL}\n`;
      fs.writeFileSync(envPath, content, "utf8");
      ok("Added DATABASE_URL to existing .env");
    }
    return;
  }

  if (!fs.existsSync(examplePath)) {
    warn(".env.example not found — writing minimal .env");
    fs.writeFileSync(envPath, `DATABASE_URL=${DB_URL}\n`, "utf8");
    ok(".env written");
    return;
  }

  let content = fs.readFileSync(examplePath, "utf8");
  content = content.replace(/^DATABASE_URL=.*$/m, `DATABASE_URL=${DB_URL}`);
  fs.writeFileSync(envPath, content, "utf8");
  ok(".env written from .env.example");
}

// ── Step 4: Frontend dependencies ─────────────────────────────────────────────

function ensureFrontendDependencies() {
  const pkgPath = path.join(FRONTEND, "package.json");
  if (!fs.existsSync(pkgPath)) {
    warn("frontend/package.json not found — skipping npm install");
    return;
  }

  const nodeModulesPath = path.join(FRONTEND, "node_modules");
  const reactMarkdownPath = path.join(nodeModulesPath, "react-markdown");
  const remarkGfmPath = path.join(nodeModulesPath, "remark-gfm");
  const remarkBreaksPath = path.join(nodeModulesPath, "remark-breaks");

  if (
    fs.existsSync(nodeModulesPath) &&
    fs.existsSync(reactMarkdownPath) &&
    fs.existsSync(remarkGfmPath) &&
    fs.existsSync(remarkBreaksPath)
  ) {
    ok("Frontend dependencies already installed");
    return;
  }

  log("Installing frontend dependencies…");
  const npm = os.platform() === "win32" ? "npm.cmd" : "npm";
  const install = runIn(FRONTEND, `${npm} install`, { timeout: 900_000 });
  if (install.status !== 0) {
    const reason = install.error
      ? `\nerror: ${install.error.message}`
      : "";
    err(`frontend npm install failed:\n${install.stdout}\n${install.stderr}${reason}`);
    process.exit(1);
  }
  ok("Frontend dependencies installed");
}

// ── Step 5: Python dependencies ───────────────────────────────────────────────

function findRealPython() {
  if (fs.existsSync(VENV_PYTHON)) {
    return VENV_PYTHON;
  }
  // On Windows, python3.exe from WindowsApps is a Store stub — it prints nothing and exits 9.
  // We iterate candidates and verify each one actually runs.
  const candidates = os.platform() === "win32"
    ? ["python", "python3", "py"]
    : ["python3", "python"];

  for (const bin of candidates) {
    const found = which(bin);
    if (!found) continue;
    // Verify it's a real interpreter (not a Windows Store stub)
    const test = run(`"${found}" -c "import sys; print(sys.version)"`, { timeout: 5000 });
    if (test.status === 0 && test.stdout.trim()) return found;
  }
  return null;
}

function ensurePython() {
  log("Checking Python…");

  const python = findRealPython();
  if (!python) {
    err("Python 3 not found. Install from https://python.org");
    process.exit(1);
  }
  ok(`Python found: ${python}`);

  const reqPath = path.join(ROOT, "requirements.txt");
  if (!fs.existsSync(reqPath)) {
    warn("requirements.txt not found — skipping pip install");
    return;
  }

  log("pip install -r requirements.txt …");
  const pip = run(`"${python}" -m pip install -r requirements.txt`, { timeout: 300_000 });
  if (pip.status !== 0) {
    err(`pip install failed:\n${pip.stdout}\n${pip.stderr}`);
    process.exit(1);
  }

  log("Ensuring ruwordnet>=0.0.6…");
  run(`"${python}" -m pip install "ruwordnet>=0.0.6" --upgrade`, { timeout: 60_000 });

  ok("Python dependencies installed");
}

// ── Step 5b: Ensure pgvector extension is installed ──────────────────────────

function ensurePgVector() {
  const check = psql("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'");
  if (check.stdout && check.stdout.includes("1")) {
    ok("pgvector extension available");
    // Make sure it's actually enabled in the target database
    psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME);
    return;
  }

  log("pgvector not found — installing…");
  const platform = os.platform();

  if (platform === "win32") {
    // Try Chocolatey first (if available and already admin)
    const choco = which("choco");
    if (choco) {
      const r = run("choco install pgvector -y --no-progress", { timeout: 120_000 });
      if (r.status === 0) {
        ok("pgvector installed via Chocolatey");
        psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME);
        return;
      }
    }

    // Install via dedicated elevated ps1 — UAC prompt will appear
    const installed = installPgVectorElevated();
    if (installed) {
      log("Activating vector extension in database…");
      const ext = psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME);
      if (ext.status === 0) {
        ok("vector extension activated in database");
      } else {
        warn(`Could not activate extension: ${ext.stderr?.slice(0, 200)}`);
      }
      return;
    }

  } else if (platform === "darwin") {
    const r = run("brew install pgvector", { timeout: 120_000 });
    if (r.status === 0) {
      ok("pgvector installed via Homebrew");
      psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME);
      return;
    }

  } else {
    const pgVerR = run("pg_config --version");
    const pgVer  = (pgVerR.stdout.match(/(\d+)/) || [, "16"])[1];
    const r = run(`apt-get install -y postgresql-${pgVer}-pgvector`, { timeout: 120_000 });
    if (r.status === 0) {
      ok("pgvector installed via apt");
      psql("CREATE EXTENSION IF NOT EXISTS vector", DB_NAME);
      return;
    }
  }

  warn([
    "Could not auto-install pgvector.",
    "  Windows: run setup as Administrator, or: choco install pgvector",
    "  macOS:   brew install pgvector",
    "  Ubuntu:  sudo apt install postgresql-16-pgvector",
  ].join("\n         "));
}

// ── Step 6: Alembic migrations ────────────────────────────────────────────────

function tablesExist() {
  // Returns true if the messages table exists in the database
  const r = psql(
    "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='messages'",
    DB_NAME,
  );
  return r.stdout && r.stdout.includes("1");
}

function runMigrations() {
  log("Running alembic upgrade head…");

  const python = findRealPython();
  const alembic = run(`"${python}" -m alembic upgrade head`, {
    env: { ...process.env, DATABASE_URL: DB_URL },
  });

  if (alembic.status !== 0) {
    err(`alembic upgrade head failed:\n${alembic.stderr}\n${alembic.stdout}`);
    process.exit(1);
  }

  // Hard check: verify the messages table actually exists
  if (!tablesExist()) {
    err([
      "Migrations ran without error but the 'messages' table is still missing.",
      "This usually means pgvector failed silently. Retrying with pgvector re-install…",
    ].join("\n  "));

    ensurePgVector();

    log("Retrying alembic upgrade head after pgvector install…");
    // Reset alembic state so migrations run from scratch
    run(`"${python}" -m alembic downgrade base`, {
      env: { ...process.env, DATABASE_URL: DB_URL },
    });
    const retry = run(`"${python}" -m alembic upgrade head`, {
      env: { ...process.env, DATABASE_URL: DB_URL },
    });
    if (retry.status !== 0 || !tablesExist()) {
      err(`Migrations failed after pgvector install:\n${retry.stderr}`);
      process.exit(1);
    }
  }

  ok("Database migrations applied — messages table verified");
}

// ── Password resolution ───────────────────────────────────────────────────────

function askPassword(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

    // Hide input on supported terminals
    if (process.stdin.isTTY) {
      process.stdout.write(question);
      process.stdin.setRawMode(true);
      let input = "";
      process.stdin.resume();
      process.stdin.setEncoding("utf8");
      process.stdin.on("data", function handler(ch) {
        if (ch === "\n" || ch === "\r" || ch === "\u0003") {
          process.stdin.setRawMode(false);
          process.stdin.pause();
          process.stdin.removeListener("data", handler);
          process.stdout.write("\n");
          rl.close();
          resolve(input);
        } else if (ch === "\u007f") {
          input = input.slice(0, -1);
        } else {
          input += ch;
        }
      });
    } else {
      rl.question(question, (answer) => { rl.close(); resolve(answer); });
    }
  });
}

function resolvePasswordFromEnv() {
  const envPath = path.join(ROOT, ".env");
  if (!fs.existsSync(envPath)) return null;
  const content = fs.readFileSync(envPath, "utf8");
  const match = content.match(/^DATABASE_URL=.*:(.+)@localhost/m);
  if (match) {
    // Extract password from postgresql+asyncpg://user:PASSWORD@host/db
    const urlMatch = content.match(/DATABASE_URL=postgresql[^:]*:\/\/[^:]+:([^@]+)@/m);
    if (urlMatch) return decodeURIComponent(urlMatch[1]);
  }
  return null;
}

async function resolvePassword() {
  // 1. Try to get from existing .env
  const fromEnv = resolvePasswordFromEnv();
  if (fromEnv) {
    DB_PASSWORD = fromEnv;
    DB_URL = `postgresql+asyncpg://${DB_USER}:${encodeURIComponent(DB_PASSWORD)}@localhost:${DB_PORT}/${DB_NAME}`;
    log(`Using postgres password from .env`);
    return;
  }

  // 2. Try default "1234" silently
  const testDefault = run(
    `"${findPostgresBinWinSafe() ? path.join(findPostgresBinWinSafe(), "psql.exe") : "psql"}" -U ${DB_USER} -h localhost -p ${DB_PORT} -c "SELECT 1;" -d postgres`,
    { env: { ...process.env, PGPASSWORD: "1234" } }
  );
  if (testDefault.status === 0) {
    DB_PASSWORD = "1234";
    DB_URL = `postgresql+asyncpg://${DB_USER}:1234@localhost:${DB_PORT}/${DB_NAME}`;
    ok("Connected with default password");
    return;
  }

  // 3. Ask the user once
  console.log("\n\x1b[33mPostgreSQL is already installed on this machine.\x1b[0m");
  console.log("Your Own needs to create a database on it.");
  console.log(`Enter the password for the \x1b[1mpostgres\x1b[0m superuser:\n`);
  const pwd = await askPassword("  postgres password: ");

  if (!pwd) {
    err("No password provided — cannot connect to PostgreSQL.");
    process.exit(1);
  }

  // Verify it works
  const psqlBin = findPostgresBinWinSafe()
    ? path.join(findPostgresBinWinSafe(), "psql.exe")
    : "psql";
  const test = run(
    `"${psqlBin}" -U ${DB_USER} -h localhost -p ${DB_PORT} -c "SELECT 1;" -d postgres`,
    { env: { ...process.env, PGPASSWORD: pwd } }
  );
  if (test.status !== 0) {
    err("Wrong password — could not connect to PostgreSQL.");
    process.exit(1);
  }

  DB_PASSWORD = pwd;
  DB_URL = `postgresql+asyncpg://${DB_USER}:${encodeURIComponent(pwd)}@localhost:${DB_PORT}/${DB_NAME}`;
  ok("Password accepted");
}

// ── Free ports before starting ────────────────────────────────────────────────

function freePorts() {
  const ports = [3000, 8000];
  const platform = os.platform();

  for (const port of ports) {
    if (platform === "win32") {
      // Find PID listening on the port, then kill it
      const find = run(`netstat -ano | findstr ":${port} "`);
      if (find.status !== 0 || !find.stdout.trim()) continue;

      const pids = new Set(
        find.stdout
          .split("\n")
          .filter((l) => l.includes("LISTENING"))
          .map((l) => l.trim().split(/\s+/).pop())
          .filter(Boolean)
      );

      for (const pid of pids) {
        const kill = run(`taskkill /PID ${pid} /F`);
        if (kill.status === 0) log(`Freed port ${port} (killed PID ${pid})`);
      }
    } else {
      // macOS / Linux
      const find = run(`lsof -ti :${port}`);
      if (find.status !== 0 || !find.stdout.trim()) continue;
      const pids = find.stdout.trim().split("\n");
      for (const pid of pids) {
        run(`kill -9 ${pid}`);
        log(`Freed port ${port} (killed PID ${pid})`);
      }
    }
  }
}

// ── pgvector elevated installer ───────────────────────────────────────────────
// Runs only the pgvector install+restart steps inside an elevated ps1.
// Called from ensurePgVector() when on Windows.

function installPgVectorElevated() {
  const pgBin = findPostgresBinWinSafe();
  if (!pgBin) { warn("PostgreSQL bin dir not found"); return false; }

  const pgRoot    = path.dirname(pgBin);
  const pgVersion = path.basename(pgRoot);
  const pgTagMap  = { "17": "0.8.2_17.6", "16": "0.8.2_16.1", "15": "0.8.2_15.14", "14": "0.8.2_14.20" };
  const pgTag     = pgTagMap[pgVersion] || `0.8.2_${pgVersion}.0`;
  const url       = `https://github.com/andreiramani/pgvector_pgsql_windows/releases/download/${pgTag}/vector.v0.8.2-pg${pgVersion}.zip`;
  const pgLibDir  = path.join(pgRoot, "lib");
  const pgExtDir  = path.join(pgRoot, "share", "extension");
  const svcName   = detectPgServiceName() || `postgresql-x64-${pgVersion}`;

  const tmp       = os.tmpdir();
  const zipPath   = path.join(tmp, "pgvector.zip");
  const extPath   = path.join(tmp, "pgvector_extracted");
  const doneFlag  = path.join(tmp, "pgvector_done.txt");
  const logFile   = path.join(tmp, "pgvector_install.log");
  const ps1Path   = path.join(tmp, "pgvector_install.ps1");

  if (fs.existsSync(doneFlag)) fs.unlinkSync(doneFlag);

  // Use forward-slash versions so single-quoted ps1 paths are unambiguous
  const zip  = zipPath.replace(/\\/g, "/");
  const ext  = extPath.replace(/\\/g, "/");
  const lib  = pgLibDir.replace(/\\/g, "/");
  const extd = pgExtDir.replace(/\\/g, "/");
  const done = doneFlag.replace(/\\/g, "/");
  const logf = logFile.replace(/\\/g, "/");

  const ps1lines = [
    "$ErrorActionPreference = 'Stop'",
    `Start-Transcript -Path '${logf}' -Force`,
    `Write-Host '[pgvector] install pg${pgVersion} started'`,
    `[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12`,
    `Write-Host '[pgvector] downloading ${url}'`,
    `(New-Object Net.WebClient).DownloadFile('${url}', '${zip}')`,
    `Write-Host "[pgvector] downloaded ok"`,
    `if (Test-Path '${ext}') { Remove-Item '${ext}' -Recurse -Force }`,
    `Add-Type -Assembly System.IO.Compression.FileSystem`,
    `[IO.Compression.ZipFile]::ExtractToDirectory('${zip}', '${ext}')`,
    `Write-Host '[pgvector] extracted'`,
    `Get-ChildItem '${ext}' -Recurse | ForEach-Object {`,
    `  if ($_.Extension -eq '.dll')     { Copy-Item $_.FullName '${lib}' -Force; Write-Host "  dll  $($_.Name)" }`,
    `  if ($_.Extension -eq '.control') { Copy-Item $_.FullName '${extd}' -Force; Write-Host " ctrl $($_.Name)" }`,
    `  if ($_.Name -match '^vector.*[.]sql$') { Copy-Item $_.FullName '${extd}' -Force; Write-Host "  sql  $($_.Name)" }`,
    `}`,
    `Write-Host '[pgvector] restarting service ${svcName}'`,
    `Restart-Service -Name '${svcName}' -Force`,
    `Start-Sleep -Seconds 3`,
    `Write-Host '[pgvector] service restarted'`,
    `Stop-Transcript`,
    `Set-Content -Path '${done}' -Value 'ok'`,
  ];

  fs.writeFileSync(ps1Path, ps1lines.join("\r\n"), "utf8");

  log(`Installing pgvector for PostgreSQL ${pgVersion}…`);
  log(`Full log: ${logFile}`);
  log("UAC prompt — please click Yes…");

  // Escape ps1Path for the outer powershell -Command string
  const ps1Esc = ps1Path.replace(/\\/g, "\\\\").replace(/'/g, "\\'");
  const elevate = [
    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
    `Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \\"${ps1Esc}\\"' -Verb RunAs -Wait`,
  ].join(" ");

  run(elevate, { timeout: 180_000 });

  if (fs.existsSync(doneFlag)) {
    fs.unlinkSync(doneFlag);
    ok("pgvector installed and PostgreSQL restarted");
    spawnSync("powershell", ["-NoProfile", "-Command", "Start-Sleep -Seconds 3"], { shell: true });
    return true;
  }

  if (fs.existsSync(logFile)) {
    warn(`pgvector install failed. Last 2000 chars of log:\n${fs.readFileSync(logFile, "utf8").slice(-2000)}`);
  } else {
    warn("pgvector install failed — UAC may have been denied or log not created");
  }
  return false;
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log("\n\x1b[1m Your Own — first-run setup\x1b[0m\n");

  freePorts();
  ensurePostgres();
  await resolvePassword();
  ensureDatabase();
  ensureEnv();
  ensureFrontendDependencies();
  ensurePython();
  ensurePgVector();
  runMigrations();

  console.log("\n\x1b[32m Setup complete. Starting app…\x1b[0m\n");
}

main().catch((e) => { console.error(e); process.exit(1); });
