"""Review operations: list, approve (promote), reject.

Approving is a tier-3 write and is refused without explicit confirmation,
mirroring the confirm gate the rest of engram enforces.

A memory reaches review down one of two roads. The bridge routes a candidate to
the queue, which wraps it in an envelope carrying the proposed destination and a
reason. Or it simply sits in the registry as ``pending`` - the state every
capture starts in - or as ``superseded`` after a newer fact contradicted it. All
three are awaiting the same human yes/no, so the verbs here accept all three;
the queue envelope supplies the destination and reason when one exists, but its
absence is not a refusal.
"""

from __future__ import annotations

import contextlib
import datetime as dt

from engram.core import atomic, screen
from engram.core.locking import store_lock
from engram.core.schema import Memory, Status
from engram.core.store import MarkdownStore, Store

AWAITING_REVIEW = (Status.pending, Status.stale, Status.superseded)


def pending_reviews(store: Store) -> list[dict]:
    """List awaiting-review memories in registry order, envelope-joined by id when one exists.

    A live envelope surfaces its memory regardless of status - a promoted fact
    under dispute stays visible until the envelope is resolved, since the
    envelope's reason is what explains why it needs another look.

    The fact text comes from the registry, matching what approve and reject will
    act on; the envelope contributes its dest and reason. An envelope whose fact
    has since been compacted into the archive keeps its row, flagged ``orphan``
    and carrying the frozen snapshot, since nothing else would ever show it again.

    Both reads run under one lock: a writer completing between an unlocked
    queue_list() and an unlocked list() could join a fresh envelope set against
    a stale registry snapshot (or vice versa), surfacing a row that no longer
    matches either file on disk.
    """
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        envelopes = {
            item["memory"]["id"]: item
            for item in store.queue_list()
            if isinstance(item.get("memory"), dict) and "id" in item["memory"]
        }
        items: list[dict] = []
        joined: set[str] = set()
        for memory in store.list():
            envelope = envelopes.get(memory.id)
            if envelope is not None:
                joined.add(memory.id)
                items.append({**envelope, "memory": memory.as_item(), "envelope": True})
            elif memory.status in AWAITING_REVIEW:
                reason = memory.status.value
                items.append({"memory": memory.as_item(), "reason": reason, "envelope": False})
        for memory_id, envelope in envelopes.items():
            if memory_id not in joined:
                items.append({**envelope, "envelope": True, "orphan": True})
    return items


def _awaiting(store: Store, memory_id: str) -> tuple[Memory, str] | dict:
    """Resolve a memory awaiting review to ``(memory, dest)``, or an error dict.

    The fact itself always comes from the registry, never from the queue
    envelope. The envelope holds a snapshot taken when the item was filed, and
    the registry is the documented source of truth that the user is invited to
    hand-edit; promoting the snapshot would silently revert their edit and would
    let an envelope outlive a rejection and resurrect it. Only ``dest`` and the
    reason are the envelope's to give.
    """
    memory = store.get(memory_id)
    if memory is None:
        return {"ok": False, "error": f"no memory {memory_id}"}
    if memory.status == Status.promoted:
        return {"ok": False, "error": f"memory {memory_id} is already promoted"}
    if memory.status not in AWAITING_REVIEW:
        return {
            "ok": False,
            "error": (
                f"memory {memory_id} was rejected; "
                f'capture it again with `engram remember "<fact>"` to promote it'
            ),
        }
    item = store.queue_get(memory_id)
    dest = (item.get("dest") if item else None) or memory.dest or "memory.md"
    return memory, dest


def approve(store: Store, memory_id: str, *, confirm: bool, today: dt.date | None = None) -> dict:
    if not confirm:
        return {"ok": False, "error": "tier-3 write requires confirmation (pass --confirm)"}
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        resolved = _awaiting(store, memory_id)
        if isinstance(resolved, dict):
            return resolved
        candidate, dest = resolved
        today = today or dt.date.today()
        memory = candidate.model_copy(
            update={"status": Status.promoted, "last_verified": today, "dest": dest}
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
        try:
            store.resolve_queue(memory_id)
        except Exception:
            # Promotion and queue resolution must land together. If resolving the
            # queue item fails, undo the registry promotion so the fact stays
            # pending in the queue rather than promoted-yet-still-queued.
            if undo_token and root is not None:
                atomic.restore_from_bak(undo_token, root=root)
            raise

        # A curated approval writes only the registry; appending to the low-risk
        # log would duplicate the sensitive fact into an auto-captured surface it
        # never belongs in. The audit entry stays traceable without the text.
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
        # The capture screen is the only credential control in the system, and a
        # fact can reach here without having passed it (staged before the screen
        # existed, forced through, or hand-added to the registry). Re-check at the
        # one point a human is looking, and say so rather than silently allowing.
        warning = screen.sensitivity(memory.fact)
        result = {"ok": True, "id": memory.id}
        if warning:
            result["warning"] = f"this fact {warning}"
        return result


def reject(store: Store, memory_id: str, *, reason: str = "") -> dict:
    """Retire a memory the user does not want, from any state.

    Rejecting lands the same transition ``forget`` does, so it carries the same
    guarantees: one lock over both writes, an undo token back, and the registry
    write undone if the queue resolution fails - otherwise a rejection could
    half-land and leave the fact promotable again from its surviving envelope.
    """
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
        memory = store.get(memory_id)
        if memory is None and store.queue_get(memory_id) is None:
            return {"ok": False, "error": f"no memory {memory_id}"}

        undo_token = ""
        if memory is not None:
            rejected = memory.model_copy(update={"status": Status.rejected})
            if isinstance(store, MarkdownStore):
                _, write_result = store.update_with_token(rejected)
                undo_token = write_result["undo_token"]
            else:
                store.update(rejected)
        try:
            store.resolve_queue(memory_id)
        except Exception:
            if undo_token and root is not None:
                atomic.restore_from_bak(undo_token, root=root)
            raise

        if root is not None:
            atomic._append_audit(
                root,
                {
                    "ts": dt.datetime.now(dt.UTC).isoformat(),
                    "endpoint": "review/reject",
                    "entity_id": memory_id,
                    "path": str(store.registry) if hasattr(store, "registry") else "",
                    "undo_token": undo_token,
                    "created": False,
                },
            )
        return {"ok": True, "id": memory_id, "reason": reason, "undo_token": undo_token}


def forget(store: Store, memory_id: str) -> dict:
    """Retract a promoted fact by marking it rejected.

    Operates only on promoted memories. Returns the undo token for the memory.md
    write so that restore_from_bak with that token reinstates the promoted status.
    A separate audit entry tagged endpoint=fact/forget is appended so the action
    is traceable by endpoint name without conflating it with routine store/save writes.
    """
    root = getattr(store, "root", None)
    lock = store_lock(root) if root is not None else contextlib.nullcontext()
    with lock:
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
        try:
            store.resolve_queue(memory_id)
        except Exception:
            if undo_token and root is not None:
                atomic.restore_from_bak(undo_token, root=root)
            raise

        # A dedicated audit entry keeps the forget action traceable by endpoint.
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
