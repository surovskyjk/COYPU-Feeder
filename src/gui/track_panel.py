"""
Track selection panel — shown after a railway is fetched.
User can choose to export all tracks or select specific ones.
When a specific track is clicked, fires on_highlight(index).
"""

import tkinter as tk
from typing import Callable, Optional
import customtkinter as ctk


class TrackPanel(ctk.CTkFrame):
    def __init__(self, parent, on_highlight: Optional[Callable] = None):
        super().__init__(parent)
        self._tracks = []
        self._check_vars: list[tk.BooleanVar] = []
        self._on_highlight = on_highlight
        self._selected_idx: Optional[int] = None
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Track Selection", font=("Helvetica", 14, "bold")).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 4)
        )

        mode_frame = ctk.CTkFrame(self, fg_color="transparent")
        mode_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        mode_frame.grid_columnconfigure(0, weight=1)
        mode_frame.grid_columnconfigure(1, weight=1)

        self._mode_var = tk.StringVar(value="all")
        ctk.CTkRadioButton(
            mode_frame, text="All tracks", variable=self._mode_var,
            value="all", command=self._on_mode_change,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkRadioButton(
            mode_frame, text="Select tracks", variable=self._mode_var,
            value="select", command=self._on_mode_change,
        ).grid(row=0, column=1, sticky="w")

        self._scroll = ctk.CTkScrollableFrame(self, height=120)
        self._scroll.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._scroll.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._scroll, text="No railway loaded.", text_color="gray", font=("Helvetica", 11)
        ).grid(row=0, column=0, pady=4)

    def populate(self, tracks):
        self._tracks = tracks
        self._check_vars.clear()
        self._selected_idx = None
        for w in self._scroll.winfo_children():
            w.destroy()

        if not tracks:
            ctk.CTkLabel(
                self._scroll, text="No tracks found.", text_color="gray"
            ).grid(row=0, column=0, pady=4)
            return

        for i, track in enumerate(tracks):
            var = tk.BooleanVar(value=True)
            row_frame = ctk.CTkFrame(self._scroll, fg_color="transparent")
            row_frame.grid(row=i, column=0, sticky="ew", pady=1)
            row_frame.grid_columnconfigure(0, weight=1)

            cb = ctk.CTkCheckBox(
                row_frame,
                text=f"{track.name}  ({len(track.nodes)} nodes)",
                variable=var,
                font=("Helvetica", 11),
                command=lambda idx=i: self._on_check_change(idx),
            )
            cb.grid(row=0, column=0, sticky="w")

            # "Focus" button to highlight this track on the map
            focus_btn = ctk.CTkButton(
                row_frame, text="↗", width=28, height=22,
                font=("Helvetica", 11),
                fg_color="transparent", border_width=1,
                hover_color=["#d0d0d0", "#404040"],
                command=lambda idx=i: self._focus_track(idx),
            )
            focus_btn.grid(row=0, column=1, padx=(4, 0))

            self._check_vars.append(var)

        self._on_mode_change()

    def _on_mode_change(self):
        mode = self._mode_var.get()
        is_select = mode == "select"
        for w in self._scroll.winfo_children():
            for child in w.winfo_children() if hasattr(w, 'winfo_children') else []:
                if isinstance(child, ctk.CTkCheckBox):
                    child.configure(state="normal" if is_select else "disabled")
        # Reset highlight when switching to "all"
        if not is_select and self._on_highlight:
            self._on_highlight(None)
            self._selected_idx = None

    def _on_check_change(self, idx: int):
        pass  # just selection state change — no extra action needed

    def _focus_track(self, idx: int):
        """Highlight this track on the map; click again to deselect."""
        if self._selected_idx == idx:
            self._selected_idx = None
            if self._on_highlight:
                self._on_highlight(None)
        else:
            self._selected_idx = idx
            if self._on_highlight:
                self._on_highlight(idx)

    def get_selected_tracks(self, tracks):
        if self._mode_var.get() == "all":
            return tracks
        return [t for t, var in zip(tracks, self._check_vars) if var.get()]
