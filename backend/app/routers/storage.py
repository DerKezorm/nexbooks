"""The storage card of the settings: what the data folder holds, where it lies, how much room is left. Operator only,
from the browser (``deps.require_operator``)."""

from __future__ import annotations

from typing import Any

import anyio
from fastapi import APIRouter

from ..deps import OperatorAccount
from ..services import space

router = APIRouter(prefix="/api/v1/storage", tags=["storage"])


@router.get("", summary="What the data folder holds and where it lies")
async def overview(_operator: OperatorAccount) -> dict[str, Any]:
    """Sizes of the database and the backups, the free room, and whether the data folder lies on a network share. The
    books are not counted: nexbooks only reads their folders. Counting a large folder may take a moment, so a count is
    kept for five minutes."""
    return await anyio.to_thread.run_sync(space.overview)
