"""Review queue operations: list, approve (promote), reject.

Approving a queued memory is a tier-3 write and is refused without explicit
confirmation, mirroring the confirm gate the rest of engram enforces.
"""

from __future__ import annotations

import datetime as dt

from engram.core.schema import Memory, Status
from engram.core.store import Store


def pending_reviews(store: Store) -> list[dict]:
    return store.queue_list()


def approve(store: Store, memory_id: str, *, confirm: bool, today: dt.date | None = None) -> dict:
    if not confirm:
        return {"ok": False, "error": "tier-3 write requires confirmation (pass --confirm)"}
    item = store.queue_get(memory_id)
    if item is None:
        return {"ok": False, "error": f"no queued memory {memory_id}"}
    today = today or dt.date.today()
    memory = Memory.from_item(item["memory"]).model_copy(
        update={
            "status": Status.promoted,
            "last_verified": today,
            "dest": item.get("dest") or "memory.md",
        }
    )
    store.update(memory)
    store.append_log(memory)
    store.resolve_queue(memory_id)
    return {"ok": True, "id": memory.id}


def reject(store: Store, memory_id: str, *, reason: str = "") -> dict:
    item = store.queue_get(memory_id)
    if item is None:
        return {"ok": False, "error": f"no queued memory {memory_id}"}
    memory = store.get(memory_id)
    if memory is not None:
        store.update(memory.model_copy(update={"status": Status.rejected}))
    store.resolve_queue(memory_id)
    return {"ok": True, "id": memory_id, "reason": reason}
