"""
Routes Screen for RouteOps Curses TUI - Preference tabs with horizontal scroll.
"""

import curses
from typing import List, Dict, Tuple, Optional

from routeops.state import AppState
from routeops.models.provider import ProviderHealth
from routeops.screens.route_editor import RouteEditor
from routeops.tui.bars import (
    TIER_GREEN_PCT, TIER_YELLOW_PCT, tier_icon, tier_color,
    health_display, verdict,
)


class RoutesScreen:
    """Routes screen showing preference tabs with provider×model matrix."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.current_pref_index = 0
        self.horizontal_scroll = 0
        self.max_visible_providers = 10
        self.editor = RouteEditor(app_state)
        # Top-level view tabs (Diagnostics/Settings grammar)
        self.view_tab = 0  # 0 routes | 1 providers | 2 model test bench
        self.view_names = ["routes", "providers", "model test bench"]

    def set_size(self, width: int, height: int) -> None:
        self.width = max(60, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        # --- editor overlay takes priority when open ---
        if self.editor.mode != "closed":
            return self._handle_editor_key(key)

        prefs = list(self.app_state.config.preferences) if self.app_state.config else []
        num_prefs = len(prefs)

        if key == ord('['):  # matrix scroll left (Routes view)
            if self.view_tab == 0:
                self.horizontal_scroll = max(0, self.horizontal_scroll - 1)
            return True

        if key == ord(']'):  # matrix scroll right (Routes view)
            if self.view_tab == 0:
                num_providers = len(self.app_state.providers) if self.app_state.providers else 0
                max_scroll = max(0, num_providers - self.max_visible_providers)
                self.horizontal_scroll = min(max_scroll, self.horizontal_scroll + 1)
            return True

        # Top-level view tabs (editor closed): 1-3 (arrows are reserved for
        # global page navigation)
        if self.editor.mode == "closed":
            if key in (ord('1'), ord('2'), ord('3')):
                idx = int(chr(key)) - 1
                if idx < len(self.view_names):
                    self.view_tab = idx
                    self.horizontal_scroll = 0
                    self.current_pref_index = 0
                return True

            # Providers view: ↑/↓ select a provider in the list
            if self.view_tab == 1:
                ed = self.editor
                if key in (curses.KEY_UP, ord('k')):
                    ed.pick_idx = max(0, ed.pick_idx - 1)
                    return True
                if key in (curses.KEY_DOWN, ord('j')):
                    provs = ed._cfg_provs() if ed.ed else []
                    ed.pick_idx = min(max(0, len(provs) - 1), ed.pick_idx + 1)
                    return True
            # Model Test Bench view: space toggle, r run, x clear, ↑/↓ slot
            if self.view_tab == 2:
                ed = self.editor
                if key == ord(' '):
                    ed.bench_toggle_selected()
                    return True
                if key == ord('r'):
                    ed.bench_run()
                    return True
                if key == ord('x'):
                    ed.bench_clear()
                    return True
                if key in (curses.KEY_UP, ord('k')):
                    ed.bench_idx = max(0, ed.bench_idx - 1)
                    return True
                if key in (curses.KEY_DOWN, ord('j')):
                    ed.bench_idx = min(len(getattr(ed, 'bench', []) or []) - 1, ed.bench_idx + 1)
                    return True

        if key == ord('	'):  # Tab: next preference (Routes view)
            if num_prefs > 0 and self.view_tab == 0:
                self.current_pref_index = (self.current_pref_index + 1) % num_prefs
                self.horizontal_scroll = 0
            return True

        # --- Pending preference moves: Enter=save (diff gate), Esc=cancel ---
        # Must run BEFORE the generic Esc return so Esc cancels a staged
        # change instead of popping nav.
        if self.editor.pending is not None and self.editor.mode == "closed":
            if key in (10, 13, curses.KEY_ENTER):
                self.editor.stage_save_edit()
                return True
            if key == 27:
                self.editor.pending = None
                self.editor.msg = ""
                return True

        if key in (27, ord('h'), ord('q')):  # Esc, h, q
            return False  # Let parent handle

        # --- Phase 1B editor keys (only when editor closed) ---
        if key == ord('e'):
            self.editor.open_edit(self.current_pref_index)
            return True
        if key == ord('a'):
            self.editor.open_add()
            return True
        if key == ord('d'):
            self.editor.stage_delete(self.current_pref_index)
            return True
        if key == ord('o'):
            self.editor.open_prov_mgmt()
            return True
        if key == ord('x'):
            self.editor.test_current_route(
                self.current_pref_index, self.editor.route_idx)
            return True
        if key == ord('n'):
            # R10: rename current preference (staged through diff gate)
            self.editor.open_rename(self.current_pref_index)
            return True
        if key == ord('g'):
            # B-6: toggle routing strategy (configured order <-> LKGP) via
            # the same staged-diff gate as every other config edit.
            # ('s' is reserved globally for Settings — do not shadow it.)
            self.editor.stage_strategy_toggle()
            return True

        # --- Preference reorder: j/k move pref in fallback order ---
        if key in (ord('j'), ord('J')):
            self.editor.move_pref(self.current_pref_index, +1,
                                  after=lambda: self._sync_pref_index())
            return True
        if key in (ord('k'), ord('K')):
            self.editor.move_pref(self.current_pref_index, -1,
                                  after=lambda: self._sync_pref_index())
            return True

        return False

    def _sync_pref_index(self) -> None:
        """Keep the visible tab cursor on the preference we just moved."""
        if self.editor._moved_to is not None:
            self.current_pref_index = self.editor._moved_to
            self.editor._moved_to = None

    # ------------------------------------------------------- editor keys

    def _handle_editor_key(self, key: int) -> bool:
        ed = self.editor
        if ed.mode == "key_mgmt":
            if key in (curses.KEY_UP, ord('k')):
                ed.km_idx = max(0, ed.km_idx - 1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                cfg = ed.ed.load()
                keys, _ = ed._keys_of(cfg, ed.km_prov)
                ed.km_idx = min(len(keys) - 1, ed.km_idx + 1)
                return True
            if key == ord('t'):
                ed.km_test(all_keys=False)
                return True
            if key == ord('T'):
                ed.km_test(all_keys=True)
                return True
            if key == ord('a'):
                ed.km_input = ""
                ed.mode = "km_add"
                return True
            if key == ord('d'):
                ed.km_stage_delete()
                return True
            if key == 27:  # Esc -> model browser for same provider
                ed.open_model_browser(ed.km_prov)
                return True
            return True
        if ed.mode == "km_add":
            if key in (10, 13, curses.KEY_ENTER):
                ed.km_stage_add(ed.km_input)
                return True
            if key in (8, 127, curses.KEY_BACKSPACE):
                ed.km_input = ed.km_input[:-1]
                return True
            if 32 <= key <= 126:
                ed.km_input += chr(key)
                return True
            if key == 27:
                ed.open_key_mgmt(ed.km_prov)
                return True
            return True
        if ed.mode == "model_browser":
            if key in (curses.KEY_UP, ord('k')):
                ed.mb_idx = max(0, ed.mb_idx - 1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.mb_idx = min(len(ed.mb_models) - 1, ed.mb_idx + 1)
                return True
            if key == ord('t'):
                ed.mb_test_model()
                return True
            if key == ord('a'):
                ed.mb_stage_pref()
                return True
            if key == ord('c'):
                ed.mb_to_bench()
                return True
            if key == 27:
                ed.mode = "prov_mgmt"  # back to provider selection, NOT routes home
                return True
            return True
        if ed.mode == "test_bench":
            if key in (curses.KEY_UP, ord('k')):
                ed.bench_idx = max(0, ed.bench_idx - 1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.bench_idx = min(len(getattr(ed, 'bench', []) or []) - 1, ed.bench_idx + 1)
                return True
            if key == ord(' '):  # toggle select slot
                ed.bench_toggle_selected()
                return True
            if key == ord('r'):
                ed.bench_run()
                return True
            if key == ord('x'):
                ed.bench_clear()
                return True
            if key == 27:
                ed.mode = "prov_mgmt"  # back to provider selection, NOT routes home
                return True
            return True
        if ed.mode == "edit":
            if key == ord('e'):
                ed.toggle_route()
                return True
            # Arrows OR j/k = cursor navigation (select which route to move)
            if key in (curses.KEY_UP, ord('k')):
                ed.move_route(-1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.move_route(1)
                return True
            # m = move selected route DOWN in fallback chain
            if key == ord('m'):
                ed.move_route_in_fallback(1)
                return True
            # u = move selected route UP in fallback chain
            if key == ord('u'):
                ed.move_route_in_fallback(-1)
                return True
            if key == ord('d'):
                ed.stage_delete(self.current_pref_index)
                return True
            if key in (10, 13, curses.KEY_ENTER):
                ed.stage_save_edit()
                return True
            if key == 27:
                ed.cancel()
                return True
            return True
        if ed.mode == "add":
            if key in (10, 13, curses.KEY_ENTER):
                if ed.input_buf.strip():
                    ed.search(ed.input_buf.strip())
                return True
            if key in (8, 127, curses.KEY_BACKSPACE):
                ed.input_buf = ed.input_buf[:-1]
                return True
            if 32 <= key <= 126:
                ed.input_buf += chr(key)
                return True
            if key == 27:
                ed.cancel()
                return True
            return True
        if ed.mode == "results":
            if key in (curses.KEY_UP, ord('k')):
                ed.result_idx = max(0, ed.result_idx - 1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.result_idx = min(len(ed.matches) - 1, ed.result_idx + 1)
                return True
            if key in (10, 13, curses.KEY_ENTER):
                ed.choose_model()
                return True
            if key == 27:
                ed.mode = "closed"
                return True
            return True
        if ed.mode == "pick_prov":
            if key in (curses.KEY_UP, ord('k')):
                ed.move_pick(-1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.move_pick(1)
                return True
            if key == ord(' '):
                ed.toggle_pick()
                return True
            if key in (10, 13, curses.KEY_ENTER):
                ed.stage_choice()
                return True
            if key == 27:
                ed.mode = "results"
                return True
            return True
        if ed.mode == "prov_mgmt":
            if key in (curses.KEY_UP, ord('k')):
                ed.pick_idx = max(0, ed.pick_idx - 1)
                return True
            if key in (curses.KEY_DOWN, ord('j')):
                ed.pick_idx = min(len(ed._cfg_provs()) - 1, ed.pick_idx + 1)
                return True
            if key == ord('m'):  # model browser for selected provider
                cfg = ed.ed.load()
                provs = ed._cfg_provs(cfg)
                if 0 <= ed.pick_idx < len(provs):
                    ed.open_model_browser(provs[ed.pick_idx]["name"])
                return True
            if key == ord('c'):  # test bench
                ed.open_bench()
                return True
            if key == 27:
                ed.cancel()
                return True
            return True
        if ed.mode == "rename_pref":
            if key in (10, 13, curses.KEY_ENTER):
                ed.stage_rename()
                return True
            if key in (8, 127, curses.KEY_BACKSPACE):
                ed.input_buf = ed.input_buf[:-1]
                return True
            if 32 <= key <= 126:
                ed.input_buf += chr(key)
                return True
            if key == 27:
                ed.cancel()
                return True
            return True
        if ed.mode in ("confirm_delete", "diff"):
            # R2: Enter and y both confirm (Settings uses Enter; keep y as
            # the legacy alias), Esc/n cancel.
            if key in (10, 13, curses.KEY_ENTER, ord('y'), ord('Y')):
                if ed.mode == "confirm_delete":
                    ed.exec_delete()
                else:
                    ed.commit_pending()
                return True
            if key in (27, ord('n'), ord('N')):
                ed.pending = None
                ed.mode = "closed"
                ed.msg = "cancelled — nothing written"
                return True
            return True
        if ed.mode == "result":
            if key in (27, 10, 13, ord('q')):
                ed.mode = "closed"
                return True
            return True
        return True

    def _render_editor_overlay(self, term, rows: int, cols: int) -> None:
        """Full-screen editor takeover, Overview-style with bottom action bar."""
        ed = self.editor
        if ed.mode == "closed":
            return

        # --- body lines per mode ---
        lines: List[str] = []
        seg_rows: Dict[int, List] = {}  # line-idx -> colored segments
        if ed.mode == "key_mgmt":
            cfg = ed.ed.load()
            keys, base_url = ed._keys_of(cfg, ed.km_prov)
            lines.append(f"KEYS — {ed.km_prov}  ({len(keys)} pool(s))   {base_url}")
            lines.append("")
            start_idx = len(lines)
            for i, k in enumerate(keys):
                sel = ">" if i == ed.km_idx else " "
                lines.append("")  # placeholder; colored below
                res = ed.km_results.get(k)
                st = ed.km_stats.get(k, {})
                if res is not None:
                    mark, mcolor = (("✅", 2) if res["ok"] else ("⚠️", 4))
                    detail = res["detail"]
                else:
                    ok, fail = st.get("ok", 0), st.get("fail", 0)
                    if ok == 0 and fail == 0:
                        mark, mcolor, detail = ("·", 0, "no recent use")
                    elif fail > 0 and ok == 0:
                        mark, mcolor, detail = ("⚠️", 4, "recent: all failures")
                    else:
                        mark, mcolor, detail = ("✅", 2, "recent: working")
                seg_rows[start_idx + i] = [
                    (f" {sel} [{i}] {k:<36}", curses.A_BOLD),
                    (mark, curses.color_pair(mcolor) | curses.A_BOLD),
                    (f"  ok:{st.get('ok',0):<4} fail:{st.get('fail',0):<4}", 0),
                    (f"  last:{(st.get('last') or '—')[11:19]}", 0),
                    (f"  {detail}" if res else "", 0),
                ]
            lines.append("")
            if getattr(ed, "km_note", None):
                lines.append(ed.km_note)
                ed.km_note = None
        elif ed.mode == "km_add":
            lines.append(f"ADD KEY to {ed.km_prov} — type env var name:")
            lines.append(f"> {ed.km_input}_")
            lines.append("")
            lines.append(f"e.g. NVIDIA_API_KEY_4 — value goes in ~/.hermes/.env,")
            lines.append(f"never in config.json or this TUI.")
        elif ed.mode == "model_browser":
            lines.append(f"MODELS — {ed.mb_prov}  ({len(ed.mb_models)} live)")
            lines.append("")
            if ed.mb_result:
                lines.append(f"  {ed.mb_result}")
                lines.append("")
            start = max(0, ed.mb_idx - (rows - 12))
            for i in range(start, min(start + rows - 12, len(ed.mb_models))):
                sel = ">" if i == ed.mb_idx else " "
                lines.append(f" {sel} {ed.mb_models[i]}")
            lines.append("")
            # Bench contents — not just a count: show what is queued
            slots = getattr(ed, "bench", []) or []
            if slots:
                lines.append(f"  BENCH ({len(slots)}/10):")
                for b, (pn, mdl) in enumerate(slots):
                    cur = " ◀" if (pn == ed.mb_prov and mdl == (ed.mb_models[ed.mb_idx] if ed.mb_models else None)) else ""
                    lines.append(f"    [{b+1}] {pn} / {mdl}{cur}")
            else:
                lines.append("  BENCH (0/10) — empty: [c] adds this model")
            lines.append(f"  [c] adds '{ed.mb_models[ed.mb_idx] if ed.mb_models else '—'}' to bench")
        elif ed.mode == "test_bench":
            lines.append("TEST BENCH — up to 10 provider/model pairs (space=toggle, r=run selected)")
            lines.append("")
            slots = getattr(ed, "bench", []) or []
            if not slots:
                lines.append("  (empty — add slots from model browser: c)")
            for i, (pn, mdl) in enumerate(slots):
                sel = ed.bench_sel[i] if getattr(ed, "bench_sel", None) else True
                mark = "[x]" if sel else "[ ]"
                cur = "▶" if i == getattr(ed, "bench_idx", 0) else " "
                lines.append(f" {cur} {mark} [{i+1}] {pn} / {mdl}")
            n_sel = sum(1 for s in (getattr(ed, "bench_sel", None) or [True]*len(slots)) if s)
            lines.append("")
            lines.append(f"  {n_sel}/{len(slots)} selected — r runs only those")
            lines.append("")
            if ed.bench_results:
                lines.append("RESULTS:")
                for pn, mdl, r in ed.bench_results:
                    verdict = "✅ OK" if r.get("ok") else f"⚠️ {r.get('detail','')[:40]}"
                    lines.append(f"  {pn}/{mdl}: {verdict}")
        elif ed.mode == "edit":
            # Render from PENDING if staged edits exist (m/u/e modify pending,
            # not disk) — otherwise the list never reflects staged moves.
            cfg = ed.pending if ed.pending is not None else ed.ed.load()
            cur = ed._current_pref(cfg)
            lines.append(f"EDIT preference '{cur['name'] if cur else '?'}'")
            lines.append(ed.msg or "")
            lines.append("")
            # show each configured route with cursor
            if cur is not None:
                for i, r in enumerate(cur["routes"]):
                    sel = ">" if i == ed.route_idx else " "
                    lines.append(f" {sel} {r['provider']:<12} {r['model']}")
            lines.append("")
        elif ed.mode == "add":
            lines.append("SEARCH — type part of a model name (fuzzy: glm 5, llama 3, qwen):")
            lines.append(f"> {ed.input_buf}_")
            lines.append("")
            if ed.probe_results:
                lines.extend(ed.probe_results[:3])
        elif ed.mode == "results":
            lines.append(ed.msg[:cols - 6])
            lines.append("")
            start = max(0, ed.result_idx - (rows - 8))
            for i in range(start, min(start + rows - 8, len(ed.matches))):
                mid, provs = ed.matches[i]
                mark = ">" if i == ed.result_idx else " "
                pl = ",".join(p[:8] for p in provs)
                lines.append(f" {mark} {mid[:44]:<44} [{pl}]")
            lines.append("")
        elif ed.mode == "pick_prov":
            lines.append(f"'{ed.chosen_model[:50]}' — which providers should serve it?")
            lines.append("")
            for i, p in enumerate(ed.chosen_provs):
                mark = "[x]" if ed.pick_on.get(p) else "[ ]"
                sel = ">" if i == ed.pick_idx else " "
                lines.append(f" {sel} {mark} {p}")
            lines.append("")
        elif ed.mode == "prov_mgmt":
            cfg = ed.ed.load()
            provs = ed._cfg_provs(cfg)
            dis = {p["name"] for p in cfg.get("disabled_providers", [])}
            lines.append(f"PROVIDERS — {len(provs)} configured "
                         f"({len(dis)} disabled)")
            lines.append("")
            for i, p in enumerate(provs):
                sel = ">" if i == ed.pick_idx else " "
                state = "[DISABLED]" if p["name"] in dis else "[active]"
                lines.append(f" {sel} {p['name']:<12} {state:<12} {p['base_url'][:44]}")
        elif ed.mode == "rename_pref":
            lines.append("RENAME PREFERENCE — type new name, Enter=stage, Esc=cancel")
            lines.append(f"> {ed.input_buf}_")
            lines.append("")
            if ed.msg:
                lines.append(ed.msg[:cols - 6])
        elif ed.mode == "confirm_delete":
            lines.append(f"DELETE preference '{ed.pending_reason}' ?")
            lines.append("This removes all its routes from config.json (backup kept).")
            lines.append("")
        elif ed.mode == "diff":
            lines.append("STAGED CHANGES — nothing written yet:")
            lines.append("")
            old = ed.ed.load()
            if ed.pending is not None:
                lines.extend(ed.ed.diff_summary(old, ed.pending)[:rows - 9])
            lines.append("")
        elif ed.mode == "result":
            lines.append(ed.msg[:cols - 6] if ed.msg else "")
            lines.append("")
            # R9: route-test results live in route_test_result; search/probe
            # results stay in probe_results.
            src = getattr(ed, "route_test_result", None) or ed.probe_results
            lines.extend([r[:cols - 6] for r in src[:rows - 8]])
            lines.append("")

        # --- action bar per mode (persistently visible, like overview) ---
        actions = {
            "edit":         "[e]=toggle  arrows/jk=cursor  m=move down  u=move up  d=delete pref  Enter=save  Esc=cancel",
            "add":          "[type]=query   [Enter]=search all catalogs   [Esc]=cancel",
            "results":      "[j/k]=select model   [Enter]=pick   [Esc]=cancel",
            "pick_prov":    "[space]=toggle provider   [Enter]=stage   [Esc]=back to results",
            "prov_mgmt":    "[j/k]=select   [m]=models   [c]=bench   [Esc]=close",
            "rename_pref":  "[type]=new name   [Enter]=stage   [Esc]=cancel",
            "key_mgmt":     "[t]=test key   [T]=test ALL   [a]=add key   [d]=delete   [Esc]=browse models",
            "km_add":       "[type]=env var name   [Enter]=stage   [Esc]=back",
            "model_browser": "[j/k]=select model   [t]=test   [a]=route it   [c]=add to bench   [Esc]=close",
            "test_bench":   "[↑/↓]=slot  [space]=select/unselect  [r]=run selected  [x]=clear  [Esc]=close",
            "confirm_delete": "[Enter/y]=DELETE   [n/Esc]=cancel",
            "diff":         "[Enter/y]=write to config.json (backup auto-made)   [n/Esc]=discard",
            "result":       "[any key]=close",
        }[ed.mode]

        # --- render: Overview-style boxed body, key help at rows-2 ---
        body_h = rows - 5  # header(2) + key help(1) + disclaimer(1) + margin
        y = 0
        title = f" ROUTE EDITOR — {ed.mode.upper()} "
        term.write(y, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1
        w = cols - 4
        term.write(y, 0, f"┌ {ed.mode.upper()} {'─' * max(0, w - len(ed.mode) - 2)}┐", curses.color_pair(1))
        shown = 0
        for line in lines:
            if shown >= body_h - 1:
                break
            if shown in seg_rows:
                # colored segment row
                term.write(y + 1 + shown, 2, "│ ", curses.color_pair(1))
                x = 4
                room = w - 2
                for text, attr in seg_rows[shown]:
                    if room <= 0 or not text:
                        break
                    term.write(y + 1 + shown, x, text[:room], attr)
                    x += len(text[:room])
                    room -= len(text[:room])
                term.write(y + 1 + shown, w + 2, " │", curses.color_pair(1))
            else:
                term.write(y + 1 + shown, 2, f"│ {line[:w - 2].ljust(w - 2)[:w - 2]} │")
            shown += 1
        term.write(y + 1 + shown, 0, f"└{'─' * w}┘", curses.color_pair(1))

        # Action bar: the mode's keys only — home key-help is a routes-home
        # element and must NOT appear inside editor overlays.
        term.write(rows - 1, 0, actions[:cols - 1], curses.A_BOLD | curses.color_pair(4))

    def _providers_view_body(self, rows: int, cols: int) -> list:
        """Providers view body (mirrors the prov_mgmt overlay)."""
        ed = self.editor
        try:
            cfg = ed.ed.load()
        except Exception:
            return ["config unreadable"]
        provs = ed._cfg_provs(cfg)
        dis = {p["name"] for p in cfg.get("disabled_providers", [])}
        lines = [f"PROVIDERS — {len(provs)} configured ({len(dis)} disabled)", ""]
        for i, p in enumerate(provs):
            sel = ">" if i == ed.pick_idx else " "
            state = "[DISABLED]" if p["name"] in dis else "[active]"
            lines.append(f" {sel} {p['name']:<14} {state:<12} {p['base_url'][:44]}")
        lines.append("")
        lines.append("[↑/↓]=select  [m]=models  [c]=bench  [Esc]=close")
        return lines

    def _bench_view_body(self, rows: int, cols: int) -> list:
        """Model Test Bench view body (mirrors the test_bench overlay)."""
        ed = self.editor
        slots = getattr(ed, "bench", []) or []
        lines = [f"{len(slots)}/10 slots queued — space=toggle, r=run selected, x=clear", ""]
        if not slots:
            lines.append("  (empty — add via [o] providers → [m] models → c)")
        for i, (pn, mdl) in enumerate(slots):
            sel = ed.bench_sel[i] if getattr(ed, "bench_sel", None) else True
            mark = "[x]" if sel else "[ ]"
            cur = "▶" if i == getattr(ed, "bench_idx", 0) else " "
            lines.append(f" {cur} {mark} [{i+1}] {pn} / {mdl}")
        n_sel = sum(1 for s in (getattr(ed, "bench_sel", None) or [True] * len(slots)) if s)
        lines.append("")
        lines.append(f"  {n_sel}/{len(slots)} selected — r runs only those")
        lines.append("")
        if getattr(ed, "bench_results", None):
            lines.append("RESULTS:")
            for pn, mdl, r in ed.bench_results:
                verdict = "✅ OK" if r.get("ok") else f"⚠️ {r.get('detail','')[:40]}"
                lines.append(f"  {pn}/{mdl}: {verdict}")
        return lines

    def render(self, term) -> None:
        """Render routes screen (or full-screen editor takeover)."""
        rows, cols = term.size.rows, term.size.cols

        # Editor takeover: full screen, skip the matrix behind it
        if self.editor.mode != "closed":
            self._render_editor_overlay(term, rows, cols)
            return

        y = 0

        prefs = list(self.app_state.config.preferences) if self.app_state.config else []

        # Show pending reorder state on tabs if moves staged
        pending_cfg = self.editor.pending if self.editor.mode == "closed" and self.editor.pending is not None else None
        if pending_cfg is not None:
            # Wrap raw dict prefs so .name/.routes attribute access keeps working
            class _PrefView:
                def __init__(self, d):
                    self.name = d.get("name", "?")
                    self.routes = [type("R", (), {"provider": r.get("provider", ""),
                                                  "model": r.get("model", "")})()
                                   for r in d.get("routes", [])]
                def __iter__(self):
                    return iter(self.routes)
            prefs = [_PrefView(d) for d in pending_cfg.get("preferences", [])]

        # Overview-standard header line
        from routeops.tui.render_std import render_header, render_box
        y = render_header(term.write, self.app_state, y, cols, rows)

        # Title line: ROUTES @3, hint right-aligned, ruled below
        title = f"ROUTES  [{len(prefs)} preferences]"
        hint = ("[←/→] page •  [1-3] view  [Tab]=pref  [e]=edit  [a]=add  "
                "[d]=del  [n]=rename  [x]=test  [o]=providers  [j/k]=order")
        short_hint = "[←/→] page •  [1-3] view  [Tab]=pref  [e]=edit  [a]=add  [d]=del  [Esc]=back"
        room = cols - (3 + len(title)) - 6
        if len(hint) > room:
            hint = short_hint if len(short_hint) <= room else "[Esc]=back"
        term.write(y, 3, title, curses.A_BOLD | curses.color_pair(1))
        term.write(y, cols - len(hint) - 1, hint[:cols - 2],
                   curses.color_pair(5) | curses.A_DIM)
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        # Top-level view tabs (editor closed) — same grammar as Diagnostics
        if self.editor.mode == "closed":
            tx = 3
            for vi, vn in enumerate(self.view_names):
                marker = f"[{vn.upper()}]" if vi == self.view_tab else f" {vn} "
                attr = (curses.color_pair(2) | curses.A_BOLD if vi == self.view_tab
                        else curses.A_DIM)
                if tx + len(marker) > cols - 2:
                    break
                term.write(y, tx, marker, attr)
                tx += len(marker) + 1
            y += 1
            term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
            y += 1

            if self.view_tab == 1:
                y = render_box(term.write, y, cols, "PROVIDERS",
                               self._providers_view_body(rows, cols), rows)
                self._render_disclaimer(term, rows - 1, cols)
                return
            if self.view_tab == 2:
                y = render_box(term.write, y, cols, "MODEL TEST BENCH",
                               self._bench_view_body(rows, cols), rows)
                self._render_disclaimer(term, rows - 1, cols)
                return

        if not prefs:
            term.write(y, 0, "No preferences configured — press [a] to add one", curses.color_pair(5))
            self._render_disclaimer(term, rows - 1, cols)
            return

        # Preference tabs — selectable row @3 (Settings/Diagnostics grammar)
        # Same spacing convention as Providers' view tabs; long names are
        # truncated with an ellipsis so the active tab always stays visible.
        tx = 3
        for i, pref in enumerate(prefs):
            name = pref.name
            max_len = max(12, cols - tx - 8)
            if len(name) > max_len:
                name = name[:max_len - 1] + "…"
            marker = f"[{name}]" if i == self.current_pref_index else f" {name} "
            attr = (curses.color_pair(2) | curses.A_BOLD if i == self.current_pref_index
                    else curses.A_DIM)
            if tx + len(marker) > cols - 2:
                break
            term.write(y, tx, marker, attr)
            tx += len(marker) + 1
        y += 1
        term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
        y += 1

        current_pref = prefs[self.current_pref_index]

        # Matrix box — header + colored cells via segment rendering
        w = cols - 4
        header, model_segs = self._build_matrix_segments(current_pref, cols - 4)
        box_title = f"ROUTES — {current_pref.name}"[:w - 6]
        term.write(y, 0, f"┌ {box_title} {'─' * max(0, w - len(box_title) - 2)}┐", curses.color_pair(1))
        my = y + 1
        # header row (bold cyan)
        term.write(my, 2, "│ ", curses.color_pair(1))
        term.write(my, 4, header[:w - 2].ljust(w - 2), curses.color_pair(5) | curses.A_BOLD)
        term.write(my, w + 2, " │", curses.color_pair(1))
        my += 1
        term.write(my, 2, f"│ {'─' * (w - 2)} │", curses.color_pair(1))
        my += 1
        # model rows: model name bold white + colored status cells
        for segs in model_segs:
            if my >= rows - 3:
                break
            term.write(my, 2, "│ ", curses.color_pair(1))
            x = 4
            room = w - 2
            for text, attr in segs:
                if room <= 0:
                    break
                term.write(my, x, text[:room], attr)
                x += len(text[:room])
                room -= len(text[:room])
            term.write(my, w + 2, " │", curses.color_pair(1))
            my += 1
        # legend
        if my < rows - 3:
            term.write(my, 2, f"│ {'─' * (w - 2)} │", curses.color_pair(1))
            my += 1
            legend = "Legend: "
            term.write(my, 2, "│ ", curses.color_pair(1))
            lx = 4
            for text, attr in [("Legend: ", curses.A_DIM),
                               ("●", curses.color_pair(2)), ("=healthy  ", curses.A_DIM),
                               ("◐", curses.color_pair(3)), ("=degraded  ", curses.A_DIM),
                               ("○", curses.color_pair(4)), ("=failing  ", curses.A_DIM),
                               ("◎", curses.A_DIM), ("=never exercised  ", curses.A_DIM),
                               ("·", curses.A_DIM), ("=not configured  ", curses.A_DIM),
                               ("(cells: JSONL per-cell, else /health)", curses.A_DIM)]:
                if lx + len(text) > w + 1:
                    break
                term.write(my, lx, text, attr)
                lx += len(text)
            term.write(my, w + 2, " │", curses.color_pair(1))
            my += 1
        # summary line (like Providers' Total:… row)
        if my < rows - 3:
            n_routed = len({r.provider for r in current_pref.routes})
            n_models = len({r.model for r in current_pref.routes})
            summary = (f"Providers in chain: {n_routed}  ·  Models: {n_models}  ·  "
                       f"[x]=test all providers × all keys")
            term.write(my, 2, "│ ", curses.color_pair(1))
            term.write(my, 4, summary[:w - 2], curses.color_pair(5) | curses.A_DIM)
            term.write(my, w + 2, " │", curses.color_pair(1))
            my += 1
        term.write(my, 0, f"└{'─' * w}┘", curses.color_pair(1))

        # Restart-needed banner — only meaningful when the auto-restart
        # FAILED. On success, _auto_restart_router() clears need_restart.
        self._render_disclaimer(term, rows - 1, cols)
        if self.editor.need_restart:
            term.write(rows - 4, 0,
                       "⚠ ROUTER RESTART FAILED — start it manually: "
                       "bash ~/CraftedWorkflowsOS/router/scripts/startr",
                       curses.color_pair(4) | curses.A_BOLD)

    def _render_disclaimer(self, term, y: int, cols: int) -> None:
        disclaimer = ("EDITOR ACTIVE  |  writes config.json with .bak backup  |  "
                      "confirmed save restarts the router automatically")
        term.write(y, 0, disclaimer[:cols], curses.color_pair(8))

    def _build_matrix_segments(self, pref, cols: int):
        """Build matrix as (header, per-model segment rows) with colored cells.
        GREEN ● healthy / YELLOW ◐ degraded / RED ○ failing / DIM · not routed.
        Cell priority: JSONL per-(provider,model) results > provider-level /health."""
        w = cols - 4
        providers = list(self.app_state.providers.keys())
        provider_health = {name: pm.health for name, pm in self.app_state.providers.items()}
        mh = getattr(self.app_state, '_model_health', {})

        # ORDER: providers routed for THIS preference first (in route order),
        # then unrouted ones last — so active columns lead the matrix
        routed = [r.provider for r in pref.routes if r.provider in providers]
        routed = list(dict.fromkeys(routed))  # dedupe, keep order
        unrouted = [p for p in providers if p not in routed]
        providers = routed + unrouted

        shown = providers[self.horizontal_scroll:self.horizontal_scroll + self.max_visible_providers]
        # Adaptive column width: shrink when many providers so the matrix fits
        col_w = 12 if len(shown) <= 5 else (10 if len(shown) <= 7 else 8)
        # "Model" padded to 15 = the model-name column width in data rows
        header = f"{'Model':<15}"
        for p in shown:
            header += f"  {p[:col_w]:<{col_w}}"

        models = []
        for route in pref.routes:
            if route.model not in models:
                models.append(route.model)

        rows_segs = []
        for model in models:
            segs = [(f"{model[:15]:<15}", curses.A_BOLD)]
            for p in shown:
                has_route = any(r.provider == p and r.model == model for r in pref.routes)
                if not has_route:
                    segs.append((f"  {'·':<{col_w}}", curses.A_DIM))
                    continue

                # 1) JSONL per-(provider, model) results — ground truth when present
                st = mh.get((p, model))
                if st:
                    r_ok = st.get("ok", 0)
                    r_fail = st.get("fail", 0)
                    total = r_ok + r_fail
                    pct = (r_ok / total * 100) if total else None
                    hv = health_display(pct, total, r_fail)
                    segs.append((f"  {hv.icon:<{col_w}}", hv.text_color | curses.A_BOLD))
                    continue

                # 2) Fall back to provider-level health — configured and routed
                #    but never exercised yet: green ● (not a red ○).
                health = provider_health.get(p)
                if health and health.healthy:
                    segs.append((f"  {'●':<{col_w}}", curses.color_pair(2) | curses.A_BOLD))
                elif health and not health.healthy:
                    segs.append((f"  {'◐':<{col_w}}", curses.color_pair(3) | curses.A_BOLD))
                else:
                    segs.append((f"  {'○':<{col_w}}", curses.color_pair(4) | curses.A_BOLD))
            rows_segs.append(segs)
        return header, rows_segs

