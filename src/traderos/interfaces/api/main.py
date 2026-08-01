from __future__ import annotations

import os

from traderos.interfaces.api.server import build_app
from traderos.interfaces.api.server import ensure_fastapi


def main() -> None:
    ensure_fastapi()
    import uvicorn

    ssl_keyfile = os.getenv("SSL_KEYFILE")
    ssl_certfile = os.getenv("SSL_CERTFILE")
    port = int(os.getenv("PORT", "8000"))
    kwargs: dict = {"host": "0.0.0.0", "port": port}
    if ssl_keyfile and ssl_certfile:
        kwargs["ssl_keyfile"] = ssl_keyfile
        kwargs["ssl_certfile"] = ssl_certfile
    app = build_app()
    uvicorn.run(app, **kwargs)


if __name__ == "__main__":
    main()
