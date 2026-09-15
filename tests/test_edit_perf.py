"""
Part A — fixing "editing a radius gets progressively slower the more curves
you've already edited this session."

`App._replay_tail` is the fix for the concrete mechanism behind that: model.log
(candidates.rebuild_pi_span's accumulating clamp/skip notes) is only ever
reset by a FULL rebuild — `_on_elements_rebuilt` used to replay the entire
accumulated list to the log panel on every single edit, so the Nth edit's
logging cost was O(N) instead of O(1).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.app import _replay_tail  # noqa: E402


class TestReplayTail:

    def test_empty_log_replays_nothing(self):
        notes, replayed = _replay_tail([], 0)
        assert notes == []
        assert replayed == 0

    def test_first_call_replays_everything_present(self):
        notes, replayed = _replay_tail(["a", "b"], 0)
        assert notes == ["a", "b"]
        assert replayed == 2

    def test_subsequent_call_replays_only_the_new_tail(self):
        log = ["a", "b"]
        notes, replayed = _replay_tail(log, 2)
        assert notes == []
        assert replayed == 2
        log.append("c")
        notes, replayed = _replay_tail(log, replayed)
        assert notes == ["c"]
        assert replayed == 3

    def test_many_sequential_appends_each_replay_only_one_entry(self):
        """The regression this exists to prevent: N sequential edits, each
        appending one note, must each cost O(1) to replay — not O(edits so
        far) — so the total work across N edits is O(N), not O(N^2)."""
        log: list[str] = []
        replayed = 0
        total_replayed_count = 0
        for i in range(50):
            log.append(f"note {i}")
            notes, replayed = _replay_tail(log, replayed)
            assert notes == [f"note {i}"]   # exactly ONE new entry each time
            total_replayed_count += len(notes)
        assert total_replayed_count == 50

    def test_log_reset_by_a_full_rebuild_replays_the_new_log_from_scratch(self):
        """rebuild_from_pi_model resets model.log to [] — a shorter log than
        what was already replayed is the signal a reset happened."""
        notes, replayed = _replay_tail(["a", "b", "c"], 0)
        assert replayed == 3
        # A full rebuild ran: model.log is now a fresh, shorter list.
        notes, replayed = _replay_tail(["x"], replayed)
        assert notes == ["x"]
        assert replayed == 1
