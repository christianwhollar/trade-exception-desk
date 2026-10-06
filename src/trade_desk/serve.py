import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Launch the trade desk, worker and browser interface"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Use synthetic data and local demo identities"
    )
    parser.add_argument("--port", type=int, default=8101)
    parser.add_argument("--data-dir", default="runtime")
    parser.add_argument(
        "--worker", action="store_true", help="Continuously investigate queued cases"
    )
    args = parser.parse_args()
    os.environ["DATA_DIR"] = args.data_dir
    if args.demo:
        os.environ["APP_DEMO"] = "1"
        from .store import Store
        from .seed import seed

        seed(Store(Path(args.data_dir) / "trades.db"))
    if args.worker:
        os.environ["DESK_WORKER"] = "1"
    import uvicorn

    uvicorn.run("trade_desk.api:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
