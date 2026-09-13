import logging

import uvicorn

from ecochronos_vault.config import get_settings


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    uvicorn.run(
        "ecochronos_vault.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
