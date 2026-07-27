from __future__ import annotations

from traderos.interfaces.api.server import build_app
from traderos.interfaces.api.server import ensure_fastapi


def main() -> None:
    ensure_fastapi()
    import uvicorn

    app = build_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
