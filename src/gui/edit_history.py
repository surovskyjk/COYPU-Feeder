"""
Bounded undo/redo history for the working PI model.

Backs the toolbar's dual-role Back/Forward buttons (see app.py
`_on_toolbar_back` / `_on_toolbar_forward`): inside Refine/Consolidate they
undo/redo alignment edits (merge, radius/spiral change, omit/restore, split,
delete, trim, PI drag) before falling back to ordinary step navigation.

Snapshots are full model copies via `geometry.candidates._light_copy`
(cheap — shares the read-only `xy_ref`/`chainages_ref` arrays by reference,
copies only V/idx/pis/elements/tangent_stubs/last_stats). Undo/redo is
user-paced, not a hot path, so a full snapshot per edit (rather than a
span-local diff) keeps this simple and easy to trust.

App owns one EditHistory per editing session; Refine/Consolidate bind the
SAME PIAlignment object, so `undo`/`redo` mutate it in place — they never
replace `model` itself (mirrors the span-rebuild convention elsewhere in the
codebase).
"""

from __future__ import annotations

from collections import deque


class EditHistory:
    def __init__(self, max_depth: int = 50):
        self._max_depth = max_depth
        self._undo: deque = deque(maxlen=max_depth)
        self._redo: deque = deque(maxlen=max_depth)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def can_undo(self) -> bool:
        return len(self._undo) > 0

    def can_redo(self) -> bool:
        return len(self._redo) > 0

    def peek_undo_label(self) -> str:
        return self._undo[-1][1] if self._undo else ""

    def peek_redo_label(self) -> str:
        return self._redo[-1][1] if self._redo else ""

    def push(self, model, label: str = "") -> None:
        """Snapshot `model`'s state BEFORE an edit is applied. Call this
        just before mutating the model, not after. Starting a fresh edit
        invalidates any redo history (standard undo/redo semantics)."""
        from geometry.candidates import _light_copy
        if model is None:
            return
        self._undo.append((_light_copy(model), label))
        self._redo.clear()

    def discard_last_push(self) -> None:
        """Cancel the most recent `push` — call this when the edit it was
        guarding turned out to be a no-op (the mutator reported failure and
        left the model unchanged), so a failed action doesn't leave a
        confusing do-nothing entry in the undo stack."""
        if self._undo:
            self._undo.pop()

    def undo(self, model) -> str | None:
        """Restore `model` in place to the last pushed snapshot. Returns
        the edit's label on success, None if there was nothing to undo."""
        if model is None or not self._undo:
            return None
        from geometry.candidates import _light_copy
        snap, label = self._undo.pop()
        self._redo.append((_light_copy(model), label))
        apply_model_snapshot(model, snap)
        return label

    def redo(self, model) -> str | None:
        """Re-apply the most recently undone edit. Returns its label on
        success, None if there was nothing to redo."""
        if model is None or not self._redo:
            return None
        from geometry.candidates import _light_copy
        snap, label = self._redo.pop()
        self._undo.append((_light_copy(model), label))
        apply_model_snapshot(model, snap)
        return label


def apply_model_snapshot(model, snap) -> None:
    """Mutate `model` in place to match `snap` (a `_light_copy`'d
    PIAlignment). Includes xy_ref/chainages_ref because trim_alignment
    reassigns those (a plain reference swap, not an in-place mutation, so
    a snapshot taken before a trim still points at the pre-trim arrays).

    Public (not just an EditHistory internal) because app.py's "Restore
    default" reuses it to apply the stashed baseline the same way undo/redo
    applies a history entry."""
    model.V             = snap.V
    model.idx           = snap.idx
    model.pis           = snap.pis
    model.xy_ref         = snap.xy_ref
    model.chainages_ref  = snap.chainages_ref
    model.elements       = snap.elements
    model.tangent_stubs  = snap.tangent_stubs
    model.last_stats     = snap.last_stats
    model.log            = snap.log
