import os
from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def mount(app):
    root = Path(__file__).parent / "web"
    app.mount("/assets", StaticFiles(directory=root), name="assets")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(root / "index.html")

    @app.get("/app-config")
    def config():
        return {
            "demo": os.getenv("APP_DEMO") == "1",
            "application": "trade-exception-desk",
            "generation": bool(os.getenv("ROUTER_URL")),
        }

    @app.get("/examples/feeds")
    def examples():
        from .seed import sample_feeds

        left, right = sample_feeds()
        return {"internal_csv": left, "counterparty_csv": right}
