/**
 * electron-builder configuration.
 * Run: npm run electron:build
 * Output goes to frontend/dist/
 */

module.exports = {
  appId: "com.yourown.app",
  productName: "Your Own",
  copyright: "AGPL-3.0",

  // Files to include in the app bundle. A packaged app is a thin client that
  // loads the remote server, so it ships only the Electron shell — not the Next
  // build, not the backend. The UI comes from https://victoraihome.com at
  // runtime; keeping .next out of the bundle is what makes this a client rather
  // than a second copy of the whole app.
  files: [
    "electron/**/*",
    "package.json",
  ],

  directories: {
    output: "dist",
    buildResources: "electron/assets",
  },

  // ── Platform targets ─────────────────────────────────────────────────────

  win: {
    target: [{ target: "nsis", arch: ["x64"] }],
    icon: "electron/assets/icon.ico",
  },

  mac: {
    target: [{ target: "dmg", arch: ["x64", "arm64"] }],
    icon: "electron/assets/icon.icns",
    category: "public.app-category.productivity",
  },

  linux: {
    target: [{ target: "AppImage", arch: ["x64"] }],
    icon: "electron/assets/icon.png",
    category: "Utility",
  },

  // ── Windows installer (NSIS) ──────────────────────────────────────────────

  nsis: {
    oneClick: true,
    perMachine: false,
    allowToChangeInstallationDirectory: false,
    deleteAppDataOnUninstall: false,
  },
};
