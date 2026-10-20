import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "deletarr.app:create_app",
        factory=True,
        host=os.getenv("DELETARR_HOST", "0.0.0.0"),
        port=int(os.getenv("DELETARR_PORT", "8980")),
        workers=1,
        proxy_headers=False,
        timeout_graceful_shutdown=90,
        access_log=False,
        log_level=os.getenv("DELETARR_LOG_LEVEL", "info"),
    )
