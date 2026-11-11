"""TOSO Console Server Launcher.

Starts the FastAPI server with real-time WebSockets and static web console UI.
Supports both local execution (127.0.0.1:8000) and container deployment (0.0.0.0:8080).
Usage:
    uv run python run_console.py
"""

import os
import sys
from pathlib import Path

import uvicorn

# Ensure src/ is in path even if run without editable install
src_path = Path(__file__).parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print("\n" + "=" * 70)
    print("  TOSO Web Operations Console")
    print(f"  Listening on: http://{host}:{port}")
    print(f"  Health probe: http://{host}:{port}/healthz")
    print("  Press Ctrl+C to stop.")
    print("=" * 70 + "\n")
    uvicorn.run(
        "toso.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level="info",
    )
