"""Verify zlapi _util patch is applied."""

from app.services import zlapi_patch  # noqa: F401
import zlapi._client as zl_client


def test_zlapi_util_patch_applied() -> None:
    assert hasattr(zl_client, "_util")
    assert callable(zl_client._util.now)
