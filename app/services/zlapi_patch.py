"""Apply runtime patches for known zlapi (zalo-api) bugs."""

from __future__ import annotations

import zlapi._client as zl_client
from zlapi import _util

# Star-import in zlapi._client skips private names; _finalize_login_session needs _util.
if not hasattr(zl_client, "_util"):
    zl_client._util = _util

# Method modules star-import _context but still miss private _util at runtime.
for _module_name in ("sendMessage", "loginWithQR"):
    try:
        import importlib

        mod = importlib.import_module(f"zlapi.method.{_module_name}")
        if not hasattr(mod, "_util"):
            mod._util = _util
    except Exception:
        pass
