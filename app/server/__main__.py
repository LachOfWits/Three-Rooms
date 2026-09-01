"""`python -m app.server` -> uvicorn on http://127.0.0.1:8600 (SPEC-APP §1)."""

from __future__ import annotations

import uvicorn

from app import config


def main() -> None:
    uvicorn.run("app.server.main:app", host=config.HOST, port=config.PORT,
                log_level="info")


if __name__ == "__main__":
    main()
