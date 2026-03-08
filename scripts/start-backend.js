#!/usr/bin/env node
/**
 * Starts the FastAPI backend from the workspace root.
 * Used by npm run electron:dev so the cwd is always correct on all platforms.
 */
"use strict";

const { spawn } = require("child_process");
const path = require("path");
const os = require("os");

const ROOT = path.resolve(__dirname, "..");
const python = os.platform() === "win32" ? "python" : "python3";

const proc = spawn(
  python,
  [
    "-m", "uvicorn", "main:app",
    "--host", "127.0.0.1",
    "--port", "8000",
    "--reload",
    "--reload-exclude", "logs",
    "--reload-exclude", "chroma_data",
  ],
  { cwd: ROOT, stdio: "inherit", shell: false }
);

proc.on("error", (err) => {
  console.error("[backend] Failed to start:", err.message);
  process.exit(1);
});

proc.on("exit", (code) => {
  process.exit(code ?? 0);
});
