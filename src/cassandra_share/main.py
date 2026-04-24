"""CLI entrypoint — `cassandra-share` or `uvicorn`."""
from __future__ import annotations

import os

import uvicorn


def cli() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "cassandra_share.app:app",
        host=host,
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    cli()
