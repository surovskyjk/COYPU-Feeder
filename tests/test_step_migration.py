"""
Wizard step-index migration across every renumbering the project format has
seen.

Three numbering schemes exist in the wild:

  * pre-1.3 — 9 steps, with Refine (4) and Consolidate (5) separate;
  * 1.3     — 8 steps, after those two merged;
  * 2.0     — 9 steps again, with Vertical Profile inserted at 7 so Export
              moved from 7 to 8.

A stored index therefore has to be walked through *both* remappings, and the
clamp at the end has to know how many steps exist today — which is why
`project_io._MAX_STEP` and `gui.app.N_STEPS` must agree.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import project_io  # noqa: E402
from project_io import _migrate_step  # noqa: E402


class TestMaxStepAgreesWithTheWizard:

    def test_max_step_matches_n_steps(self):
        """project_io is used headless and deliberately does not import the
        GUI, so nothing but this test keeps the two constants in step."""
        from gui.app import N_STEPS
        assert project_io._MAX_STEP == N_STEPS - 1

    def test_sidebar_lists_exactly_n_steps(self):
        from gui.app import N_STEPS
        from gui.step_sidebar import STEPS
        assert len(STEPS) == N_STEPS

    def test_sidebar_numbers_are_sequential(self):
        from gui.step_sidebar import STEPS
        assert [num for num, _ in STEPS] == [
            str(i + 1) for i in range(len(STEPS))]

    def test_every_step_has_a_stack_widget_and_a_tip(self):
        from gui.app import App, N_STEPS
        assert len(App._STEP_TIPS) == N_STEPS
        assert sorted(App._STEP_TIPS) == list(range(N_STEPS))


class TestPre13Files:
    """Old 9-step numbering → today's 9-step numbering, via the 1.3 scheme."""

    @pytest.mark.parametrize("stored,expected", [
        (0, 0),     # Find
        (1, 1),     # Select
        (2, 2),     # Configure
        (3, 3),     # Candidates
        (4, 4),     # Refine        → merged
        (5, 4),     # Consolidate   → merged into Refine
        (6, 5),     # Stations
        (7, 6),     # Cross-section
        (8, 8),     # Export        → 7 (v1.3) → 8 (v2.0)
    ])
    @pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
    def test_two_stage_remap(self, version, stored, expected):
        assert _migrate_step(stored, version) == expected


class Test13Files:
    """8-step numbering → 9-step numbering: only Export moves."""

    @pytest.mark.parametrize("stored,expected", [
        (0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
        (7, 8),     # Export was 7, is now 8; 7 is the new Vertical Profile
    ])
    def test_export_shifts_by_one(self, stored, expected):
        assert _migrate_step(stored, "1.3") == expected

    def test_nothing_migrates_onto_the_new_step(self):
        """Landing a legacy project on a step it has no data for would show an
        empty Vertical Profile instead of where the user actually was."""
        from gui.app import S_VERTICAL
        assert all(_migrate_step(i, "1.3") != S_VERTICAL for i in range(8))


class TestCurrentFiles:

    @pytest.mark.parametrize("stored", list(range(9)))
    def test_2_0_indices_pass_through(self, stored):
        assert _migrate_step(stored, "2.0") == stored

    def test_future_version_passes_through(self):
        assert _migrate_step(5, "9.9") == 5


class TestClamping:

    @pytest.mark.parametrize("version", ["1.0", "1.2", "1.3", "2.0"])
    def test_out_of_range_is_clamped(self, version):
        assert _migrate_step(99, version) == project_io._MAX_STEP
        assert _migrate_step(-5, version) == 0

    @pytest.mark.parametrize("version", ["", "garbage", "1", "x.y"])
    def test_unparseable_version_is_treated_as_oldest(self, version):
        """(0, 0) sorts below every real version, so an unreadable attribute
        means 'run every migration' rather than 'run none'."""
        assert _migrate_step(8, version) == 8      # Export, through both maps
        assert _migrate_step(5, version) == 4      # Consolidate → Refine

    def test_result_is_always_a_valid_step(self):
        for version in ("1.0", "1.2", "1.3", "2.0"):
            for stored in range(-3, 15):
                out = _migrate_step(stored, version)
                assert 0 <= out <= project_io._MAX_STEP
