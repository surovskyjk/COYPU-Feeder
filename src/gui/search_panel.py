"""
Search panel — two tabs:
  • Search: name search, relation ID entry, bbox fetch
  • Suggested Lines: curated list organised by country
"""

import threading
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional
import customtkinter as ctk

from data.suggested_lines import get_countries, get_lines_for_country


class SearchPanel(ctk.CTkFrame):
    def __init__(self, parent, on_result: Callable):
        super().__init__(parent)
        self._on_result = on_result
        self._results: list[dict] = []
        self._bbox: Optional[tuple] = None
        self._build()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._tabs = ctk.CTkTabview(self)
        self._tabs.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        self._tab_search = self._tabs.add("Search")
        self._tab_suggested = self._tabs.add("Suggested Lines")

        self._build_search_tab(self._tab_search)
        self._build_suggested_tab(self._tab_suggested)

    # ------------------------------------------------------------------
    # Search tab
    # ------------------------------------------------------------------

    def _build_search_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        row = 0

        # --- Name search ---
        ctk.CTkLabel(tab, text="By name:", font=("Helvetica", 12, "bold")).grid(
            row=row, column=0, sticky="w", padx=6, pady=(8, 2)
        )
        row += 1
        self._name_entry = ctk.CTkEntry(tab, placeholder_text="e.g. Praha–Brno")
        self._name_entry.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 4))
        self._name_entry.bind("<Return>", lambda e: self._search_by_name())
        row += 1

        self._search_btn = ctk.CTkButton(tab, text="Search", command=self._search_by_name)
        self._search_btn.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 6))
        row += 1

        # Results list
        ctk.CTkLabel(tab, text="Results:").grid(row=row, column=0, sticky="w", padx=6)
        row += 1
        self._listbox_frame = ctk.CTkScrollableFrame(tab, height=110)
        self._listbox_frame.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 8))
        self._listbox_frame.grid_columnconfigure(0, weight=1)
        row += 1

        # Divider
        ctk.CTkLabel(tab, text="─" * 28, text_color="gray").grid(row=row, column=0, pady=2)
        row += 1

        # --- Relation ID ---
        ctk.CTkLabel(tab, text="By relation ID:", font=("Helvetica", 12, "bold")).grid(
            row=row, column=0, sticky="w", padx=6, pady=(4, 2)
        )
        row += 1
        rel_frame = ctk.CTkFrame(tab, fg_color="transparent")
        rel_frame.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 8))
        rel_frame.grid_columnconfigure(0, weight=1)
        self._rel_entry = ctk.CTkEntry(rel_frame, placeholder_text="e.g. 3128446")
        self._rel_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._rel_entry.bind("<Return>", lambda e: self._fetch_by_relation())
        ctk.CTkButton(rel_frame, text="Fetch", width=60, command=self._fetch_by_relation).grid(
            row=0, column=1
        )
        row += 1

        # Divider
        ctk.CTkLabel(tab, text="─" * 28, text_color="gray").grid(row=row, column=0, pady=2)
        row += 1

        # --- BBox ---
        ctk.CTkLabel(
            tab, text="From bounding box:", font=("Helvetica", 12, "bold")
        ).grid(row=row, column=0, sticky="w", padx=6, pady=(4, 2))
        row += 1
        ctk.CTkLabel(
            tab, text="Draw a bbox on the map, then click below.",
            text_color="gray", font=("Helvetica", 10), wraplength=220, justify="left",
        ).grid(row=row, column=0, sticky="w", padx=6)
        row += 1
        self._bbox_status = ctk.CTkLabel(
            tab, text="No bbox drawn yet.", text_color="gray", font=("Helvetica", 10)
        )
        self._bbox_status.grid(row=row, column=0, sticky="w", padx=6, pady=(2, 4))
        row += 1
        self._bbox_fetch_btn = ctk.CTkButton(
            tab, text="Search railways in bbox",
            state="disabled", command=self._fetch_by_bbox,
            fg_color="#e67e22", hover_color="#d35400",
        )
        self._bbox_fetch_btn.grid(row=row, column=0, sticky="ew", padx=6, pady=(0, 8))

    # ------------------------------------------------------------------
    # Suggested Lines tab
    # ------------------------------------------------------------------

    def _build_suggested_tab(self, tab):
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            tab,
            text="Click a line to fetch it directly from OSM.",
            text_color="gray", font=("Helvetica", 10), wraplength=230, justify="left",
        ).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 4))

        scroll = ctk.CTkScrollableFrame(tab)
        scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        scroll.grid_columnconfigure(0, weight=1)

        row = 0
        for country in get_countries():
            ctk.CTkLabel(
                scroll, text=f"  {country}",
                font=("Helvetica", 12, "bold"), anchor="w",
            ).grid(row=row, column=0, sticky="ew", pady=(8, 2))
            row += 1

            for line in get_lines_for_country(country):
                self._add_suggested_row(scroll, row, line)
                row += 1

    def _add_suggested_row(self, parent, row: int, line: dict):
        frame = ctk.CTkFrame(parent, fg_color=["#e8e8e8", "#2b2b2b"], corner_radius=6)
        frame.grid(row=row, column=0, sticky="ew", padx=4, pady=2)
        frame.grid_columnconfigure(0, weight=1)

        name_label = ctk.CTkLabel(
            frame, text=line["name"],
            font=("Helvetica", 11), wraplength=200, justify="left", anchor="w",
        )
        name_label.grid(row=0, column=0, sticky="w", padx=8, pady=(4, 0))

        note = line.get("note", "")
        if note:
            ctk.CTkLabel(
                frame, text=note,
                font=("Helvetica", 9), text_color="gray",
                wraplength=200, justify="left", anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 2))

        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=2, column=0, sticky="w", padx=6, pady=(2, 6))

        if line.get("relation_id"):
            rid = line["relation_id"]
            ctk.CTkButton(
                btn_frame,
                text=f"Fetch (rel. {rid})",
                width=160, height=24,
                font=("Helvetica", 10),
                command=lambda r=rid: self._do_fetch(r),
            ).grid(row=0, column=0, padx=(0, 4))

        search_term = line.get("search") or line["name"].split("—")[-1].strip().split("(")[0].strip()
        ctk.CTkButton(
            btn_frame,
            text="Search by name",
            width=130, height=24,
            font=("Helvetica", 10),
            fg_color="gray", hover_color="#555",
            command=lambda s=search_term: self._prefill_search(s),
        ).grid(row=0, column=1)

    # ------------------------------------------------------------------
    # Search actions
    # ------------------------------------------------------------------

    def _search_by_name(self):
        name = self._name_entry.get().strip()
        if not name:
            return
        self._search_btn.configure(state="disabled", text="Searching…")
        self._clear_results()

        def worker():
            try:
                from osm.query import search_railways_by_name
                results = search_railways_by_name(name)
                self.after(0, lambda: self._populate_results(results))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Search error", str(exc)))
            finally:
                self.after(0, lambda: self._search_btn.configure(state="normal", text="Search"))

        threading.Thread(target=worker, daemon=True).start()

    def _fetch_by_relation(self):
        rel_text = self._rel_entry.get().strip()
        if not rel_text.isdigit():
            messagebox.showwarning("Invalid ID", "Please enter a numeric OSM relation ID.")
            return
        self._do_fetch(int(rel_text))

    def _fetch_by_bbox(self):
        if not self._bbox:
            return
        south, west, north, east = self._bbox
        self._set_busy(True)

        def worker():
            try:
                from osm.query import fetch_bbox_ways
                data = fetch_bbox_ways(south, west, north, east)
                info = {"id": None, "name": "Bbox selection", "network": "", "operator": ""}
                self.after(0, lambda: self._on_result(data, info))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Fetch error", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _do_fetch(self, relation_id: int):
        self._set_busy(True)

        def worker():
            try:
                from osm.query import fetch_relation_ways, fetch_relation_metadata
                data = fetch_relation_ways(relation_id)
                info = fetch_relation_metadata(relation_id) or {
                    "id": relation_id, "name": str(relation_id)
                }
                self.after(0, lambda: self._on_result(data, info))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Fetch error", str(exc)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()

    def _prefill_search(self, term: str):
        """Switch to Search tab and pre-fill the name entry."""
        self._tabs.set("Search")
        self._name_entry.delete(0, tk.END)
        self._name_entry.insert(0, term)
        self._search_by_name()

    # ------------------------------------------------------------------
    # Result display
    # ------------------------------------------------------------------

    def _populate_results(self, results: list[dict]):
        self._results = results
        self._clear_results()
        if not results:
            ctk.CTkLabel(
                self._listbox_frame, text="No results found.", text_color="gray"
            ).grid(row=0, column=0, pady=4)
            return
        for i, r in enumerate(results):
            label = r["name"] or f"Relation {r['id']}"
            sub = ""
            if r.get("from") and r.get("to"):
                sub = f"{r['from']} → {r['to']}"
            elif r.get("network"):
                sub = r["network"]

            frame = ctk.CTkFrame(self._listbox_frame, fg_color=["#dde", "#333"], corner_radius=4)
            frame.grid(row=i, column=0, sticky="ew", pady=2)
            frame.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(frame, text=label, font=("Helvetica", 11, "bold"),
                         anchor="w").grid(row=0, column=0, sticky="w", padx=6, pady=(3, 0))
            if sub:
                ctk.CTkLabel(frame, text=sub, font=("Helvetica", 10),
                             text_color="gray", anchor="w").grid(
                    row=1, column=0, sticky="w", padx=6, pady=(0, 1))
            ctk.CTkButton(
                frame, text="Fetch", width=50, height=22, font=("Helvetica", 10),
                command=lambda rid=r["id"]: self._do_fetch(rid),
            ).grid(row=0, column=1, rowspan=2, padx=(0, 4))

    def _clear_results(self):
        for w in self._listbox_frame.winfo_children():
            w.destroy()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_bbox(self, bbox: tuple[float, float, float, float]):
        """Called by MapPanel when the user finishes drawing a bbox."""
        self._bbox = bbox
        s, w, n, e = bbox
        self._bbox_status.configure(
            text=f"S {s:.4f}  W {w:.4f}\nN {n:.4f}  E {e:.4f}",
            text_color=["#333", "#ddd"],
        )
        self._bbox_fetch_btn.configure(state="normal")

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self._search_btn.configure(state=state)
        if self._bbox:
            self._bbox_fetch_btn.configure(state=state)
