from __future__ import annotations

import logging
import sys

from nicegui import ui

from app.ui import state as _st

# Ensure `import main` resolves to this module even when run as __main__.
sys.modules.setdefault("main", sys.modules[__name__])

logger = logging.getLogger(__name__)

ui.add_head_html(
    """
        <meta name="color-scheme" content="dark light">
        <style>
            html, body, #q-app, .q-layout, .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
            body.body--light, body.body--light #q-app, body.body--light .q-layout, body.body--light .q-page-container {
                background-color: #f8fafc !important;
                color-scheme: light;
            }
            body.body--dark, body.body--dark #q-app, body.body--dark .q-layout, body.body--dark .q-page-container {
                background-color: #0b0f14 !important;
                color-scheme: dark;
            }
        </style>
        <script>
            document.documentElement.style.backgroundColor = '#0b0f14';
            document.documentElement.classList.add('body--dark');
            if (document.body) {
                document.body.style.backgroundColor = '#0b0f14';
                document.body.classList.add('body--dark');
            } else {
                document.addEventListener('DOMContentLoaded', () => {
                    document.body.style.backgroundColor = '#0b0f14';
                    document.body.classList.add('body--dark');
                });
            }
            window.copyTableCellText = async (text) => {
                const value = String(text ?? '');
                try {
                    if (navigator?.clipboard?.writeText) {
                        await navigator.clipboard.writeText(value);
                        return true;
                    }
                } catch (error) {
                    console.warn('Clipboard API copy failed; using fallback.', error);
                }
                if (!document?.body) return false;
                const textarea = document.createElement('textarea');
                textarea.value = value;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                try {
                    return document.execCommand('copy');
                } finally {
                    document.body.removeChild(textarea);
                }
            };
        </script>
        """
)

import app.ui.pages  # noqa: F401, E402 — triggers @ui.page registration


if __name__ in {"__main__", "__mp_main__"}:  # pragma: no cover
    ui.run(
        title="VA Notify Admin",
        port=8080,
        reload=True,
        storage_secret=_st.config.master_key,
        show=False,
    )
