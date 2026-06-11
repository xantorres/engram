"""Review queue operations: list, approve (promote), reject.

Approving a queued memory is a tier-3 write and is refused without explicit
confirmation, mirroring the confirm gate the rest of engram enforces.
"""

from __future__ import annotations

import datetime as dt

from engram.core import atomic
from engram.core.schema import Memory, Status
from engram.core.store import MarkdownStore, Store


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
    try:
        if isinstance(store, MarkdownStore):
            _, write_result = store.update_with_token(memory)
            undo_token = write_result["undo_token"]
        else:
            store.update(memory)
            undo_token = ""
    except KeyError:
        return {"ok": False, "error": f"unknown memory {memory_id}"}
    store.resolve_queue(memory_id)

    # A curated approval writes only the registry; appending to the low-risk log
    # would duplicate the sensitive fact into an auto-captured surface it never
    # belongs in. The audit entry keeps the action traceable without the text.
    root = getattr(store, "root", None)
    if root is not None:
        atomic._append_audit(
            root,
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "endpoint": "review/approve",
                "entity_id": memory.id,
                "path": str(store.registry) if hasattr(store, "registry") else "",
                "undo_token": undo_token,
                "created": False,
            },
        )
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


def forget(store: Store, memory_id: str) -> dict:
    """Retract a promoted fact by marking it rejected.

    Operates only on promoted memories. Returns the undo token for the memory.md
    write so that restore_from_bak with that token reinstates the promoted status.
    A separate audit entry tagged endpoint=fact/forget is appended so the action
    is traceable by endpoint name without conflating it with routine store/save writes.
    """
    memory = store.get(memory_id)
    if memory is None:
        return {"ok": False, "error": f"no memory {memory_id}"}
    if memory.status != Status.promoted:
        return {
            "ok": False,
            "error": f"memory {memory_id} is not promoted (status={memory.status.value})",
        }

    updated = memory.model_copy(update={"status": Status.rejected})
    try:
        if isinstance(store, MarkdownStore):
            _, write_result = store.update_with_token(updated)
            undo_token = write_result["undo_token"]
        else:
            store.update(updated)
            undo_token = ""
    except KeyError:
        return {"ok": False, "error": f"concurrent write conflict for {memory_id}"}

    # Append a dedicated audit entry so the forget action is traceable by endpoint.
    root = getattr(store, "root", None)
    if root is not None:
        atomic._append_audit(
            root,
            {
                "ts": dt.datetime.now(dt.UTC).isoformat(),
                "endpoint": "fact/forget",
                "entity_id": memory_id,
                "path": str(store.registry) if hasattr(store, "registry") else "",
                "undo_token": undo_token,
                "created": False,
            },
        )

    return {"ok": True, "id": memory_id, "undo_token": undo_token}
