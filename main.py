from __future__ import annotations

import logging
import sys
from pathlib import Path

from nicegui import ui

from app.ui import state as _st

# Ensure `import main` resolves to this module even when run as __main__.
sys.modules.setdefault("main", sys.modules[__name__])

logger = logging.getLogger(__name__)

FAVICON_PATH = Path(__file__).parent / "app" / "assets" / "SpicyLlamas.webp"

import app.ui.pages  # noqa: F401, E402 — triggers @ui.page registration


if __name__ in {"__main__", "__mp_main__"}:  # pragma: no cover
    ui.run(
        title="VA Notify Admin",
        favicon=FAVICON_PATH,
        port=_st.config.port,
        reload=True,
        storage_secret=_st.config.master_key,
        show=False,
    )
