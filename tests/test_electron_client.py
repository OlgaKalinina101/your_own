"""The desktop app is a thin client to the server, not a second backend.

Run:
    python -m pytest tests/test_electron_client.py -v

The Electron shell used to run its own copy of everything: it spawned a local
FastAPI backend, built Next, and loaded ``localhost:3000``. That was the local-
first model, and it meant a packaged ``.exe`` was a whole second instance whose
data drifted from the server's.

The rewrite makes the built app load ``https://victoraihome.com`` and start
nothing of its own — the same server the phone and web talk to. These tests read
the Electron source directly (there is no way to run it under pytest) and pin
down the invariants that keep it a client:

  * production loads the remote server, development stays on localhost;
  * a packaged app never spawns a backend;
  * the build ships only the shell, not the Next build or the backend.

Any of those breaking silently would recreate the split-brain the move away from
the laptop was meant to end.
"""
from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_JS = ROOT / "frontend" / "electron" / "main.js"
BUILD_CONFIG = ROOT / "frontend" / "electron-builder.config.js"
PACKAGE_JSON = ROOT / "frontend" / "package.json"


@pytest.fixture(scope="module")
def main_src() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


class TestItLoadsTheServer:
    def test_production_points_at_the_domain(self, main_src):
        assert "https://victoraihome.com" in main_src, (
            "the built app must load the production server"
        )

    def test_the_server_url_can_be_overridden(self, main_src):
        # A domain change, or a temporary host during an outage, must not need a
        # rebuild — same lesson as the nip.io detour.
        assert "YOUR_OWN_SERVER_URL" in main_src

    def test_development_still_runs_locally(self, main_src):
        # The dev loop stays fully local; only the packaged app is a client.
        assert "localhost:${NEXT_PORT}" in main_src
        assert 'isDev ? `http://localhost' in main_src


class TestItStartsNothingOfItsOwn:
    def test_it_does_not_spawn_a_backend(self, main_src):
        # Spawning python is the whole local-first model. A packaged client must
        # not do it — the backend lives on the server.
        assert "child_process" not in main_src, "no process spawning in a thin client"
        assert "spawn(" not in main_src
        assert "uvicorn" not in main_src

    def test_the_token_is_not_read_from_a_local_file_in_production(self, main_src):
        # There is no local backend, so no local token file. The production
        # branch returns null and the user pastes the token in Settings, like
        # every other remote client.
        assert "if (!isDev) return null;" in main_src


class TestTheBundleIsJustTheShell:
    def test_the_build_does_not_ship_the_backend(self):
        config = BUILD_CONFIG.read_text(encoding="utf-8")
        # An uncommented extraResources pulling the backend in would make the
        # .exe a full instance again.
        active = "\n".join(
            line for line in config.splitlines()
            if not line.strip().startswith("//")
        )
        assert "extraResources" not in active
        assert "backend" not in active

    def test_the_build_does_not_ship_the_next_output(self):
        config = BUILD_CONFIG.read_text(encoding="utf-8")
        active = "\n".join(
            line for line in config.splitlines()
            if not line.strip().startswith("//")
        )
        # The UI is served from the remote server; a bundled .next would be a
        # stale copy shown instead.
        assert ".next" not in active

    def test_the_build_script_does_not_rebuild_next(self):
        scripts = PACKAGE_JSON.read_text(encoding="utf-8")
        # `next build` before packaging would be dead work — the shell loads the
        # server's UI, not a local build.
        assert '"electron:build": "electron-builder' in scripts

    def test_the_app_has_an_icon(self):
        assert (ROOT / "frontend" / "electron" / "assets" / "icon.ico").is_file(), (
            "the packaged app should carry its own icon, not Electron's default"
        )
