"""Apply runtime patches for known zlapi (zalo-api) bugs."""

from __future__ import annotations

import zlapi._client as zl_client
from zlapi import _util

# Star-import in zlapi._client skips private names; _finalize_login_session needs _util.
if not hasattr(zl_client, "_util"):
    zl_client._util = _util
