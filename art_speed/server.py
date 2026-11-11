"""A bare web app around the speed router, for development and tests (SP-4).

    app = server.create_app(project)        # project: a project.Project, or None

This is not the shell. SP-6 builds the real one - the window, the project folder picker, the
left rail, the page - around the same router. Until then this lets the API run on its own:

    .venv/Scripts/python.exe -m art_speed serve --project <project folder> [--number ######]

It listens on 127.0.0.1 only (port 8766; A.R.T. uses 8765). With --mockup <the design repo's
"ui mockup" folder> it also serves the fixture-driven mockup at /mockup/ with the open project's
live data in place of speed_fixtures.js - read-only: clicks in the mockup are not saved.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from art_speed import api, config, loader, project as project_mod
from art_speed.storage import db

HOST = "127.0.0.1"
PORT = 8766
VIEWER_PAGE = "speed_tab_mock_v15_fixtures.html"


def create_app(project: project_mod.Project | None = None, mockup_folder: str | None = None) -> FastAPI:
    app = FastAPI(title=config.APP_TITLE, version=config.VERSION, docs_url=None, redoc_url=None)
    app.state.project = project
    app.include_router(api.create_router(lambda: app.state.project))

    @app.get("/speed/version")
    def version():
        return {"app": config.APP_NAME, "version": config.VERSION, "db_version": db.DB_VERSION}

    def live_fixtures() -> dict | None:
        proj = app.state.project
        if proj is None or not db.exists(proj.folder, proj.number):
            return None
        conn = db.open_db(proj.folder, proj.number)
        try:
            return loader.fixtures(conn, proj)
        finally:
            conn.close()

    @app.get("/speed/fixtures")
    def fixtures():
        """The whole contract file for the open project (what `dump --project` writes)."""
        data = live_fixtures()
        if data is None:
            return JSONResponse({"ok": False, "message": "No speed data in the open project."}, status_code=404)
        return data

    if mockup_folder:
        folder = Path(mockup_folder)

        @app.get("/mockup/speed_fixtures.js")
        def fixtures_js():
            data = live_fixtures()
            text = "window.SPEED_FIXTURES=" + (json.dumps(data, separators=(",", ":")) if data else "null") + ";\n"
            return Response(text, media_type="application/javascript")

        @app.get("/mockup/")
        def viewer():
            page = folder / VIEWER_PAGE
            if not page.is_file():
                return JSONResponse({"ok": False, "message": f"{VIEWER_PAGE} is not in {folder}."}, status_code=404)
            return Response(page.read_text(encoding="utf-8"), media_type="text/html")

    return app
