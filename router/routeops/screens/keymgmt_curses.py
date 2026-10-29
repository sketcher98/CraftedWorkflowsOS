"""
Key Management Screen for RouteOps Curses TUI - Add/remove/disable keys per provider.
"""

import curses
import json
import time
from datetime import datetime
from typing import Optional, Dict, List
from routeops.state import AppState
from routeops.adapters.config_editor import ConfigEditor


class KeyManagementScreen:
    """Key management screen - add/remove/test keys per provider."""

    def __init__(self, app_state: AppState):
        self.app_state = app_state
        self.scroll_offset = 0
        self.selected_provider = 0
        self.selected_key = 0
        self.mode = "list"  # list, add_key, confirm_remove
        self.new_key_env = ""
        self.new_key_value = ""
        self.input_field = 0  # 0=key_env, 1=key_value
        self.show_detail = False
        self.last_fetch = 0
        self.fetch_interval = 10
        self.message = ""
        self.message_color = curses.color_pair(5)

    def set_size(self, width: int, height: int) -> None:
        self.width = max(70, width)
        self.height = max(15, height)

    def handle_key(self, key: int) -> bool:
        """Handle key press. Returns True if handled."""
        providers = self._get_sorted_providers()

        if self.mode == "add_key":
            if key == 27:  # Esc
                self.mode = "list"
                self.new_key_env = ""
                self.new_key_value = ""
                self.input_field = 0
                return True
            elif key in (9, curses.KEY_BTAB, curses.KEY_DOWN):  # Tab or Down
                self.input_field = 1
                return True
            elif key in (curses.KEY_UP,):  # Up
                self.input_field = 0
                return True
            elif key in (10, curses.KEY_ENTER):  # Enter
                if self.input_field == 0:
                    self.input_field = 1
                else:
                    self._add_key(providers)
                return True
            elif key in (curses.KEY_BACKSPACE, 127, 8):  # Backspace
                if self.input_field == 0:
                    self.new_key_env = self.new_key_env[:-1]
                else:
                    self.new_key_value = self.new_key_value[:-1]
                return True
            elif 32 <= key <= 126:  # Printable
                ch = chr(key)
                if self.input_field == 0:
                    self.new_key_env += ch
                else:
                    self.new_key_value += ch
                return True
            return True

        elif self.mode == "confirm_remove":
            if key in (ord('y'), ord('Y')):
                self._remove_key(providers)
                return True
            elif key in (27, ord('n'), ord('N'), ord('q')):  # Esc, n, q
                self.mode = "list"
                return True
            return True

        elif self.show_detail:
            # First Esc when scoped from a provider's detail returns False so
            # nav pops back to that provider — not to the all-providers list.
            if key == 27 and getattr(self, "_entered_from_detail", False):
                self._entered_from_detail = False
                return False
            if key in (curses.KEY_UP, ord('k')):
                self.selected_key = max(0, self.selected_key - 1)
                return True
            elif key in (curses.KEY_DOWN, ord('j')):
                provider = providers[self.selected_provider] if providers else None
                if provider:
                    keys = self._get_provider_keys(provider)
                    self.selected_key = min(len(keys) - 1, self.selected_key + 1)
                return True
            elif key in (27, ord('h'), ord('q'), 10, curses.KEY_ENTER):  # Esc, h, q, Enter
                self.show_detail = False
                self.mode = "list"
                return True
            elif key == ord('d'):  # Delete key
                self.mode = "confirm_remove"
                return True
            elif key == ord('t'):  # Test key
                self._test_selected_key(providers)
                return True
            return True

        else:
            # List mode
            max_index = len(providers) - 1 if providers else 0
            if key in (curses.KEY_UP, ord('k')):
                self.selected_provider = max(0, self.selected_provider - 1)
                return True
            elif key in (curses.KEY_DOWN, ord('j')):
                self.selected_provider = min(max_index, self.selected_provider + 1)
                return True
            elif key in (10, curses.KEY_ENTER):  # Enter
                if providers:
                    self.show_detail = True
                    self.selected_key = 0
                return True
            elif key == ord('a'):  # Add key
                self.mode = "add_key"
                self.new_key_env = ""
                self.new_key_value = ""
                self.input_field = 0
                return True
            elif key == ord('t'):  # Test all keys
                self._test_all_keys(providers)
                return True
            elif key == ord('r'):  # Refresh
                self._fetch_config()
                return True
            elif key == ord('d') and self.show_detail:  # Delete (in detail mode)
                self.mode = "confirm_remove"
                return True
            return False

    def _get_sorted_providers(self) -> List[str]:
        """Get providers sorted by routed count."""
        if not self.app_state.config or not self.app_state.config.preferences:
            return list(self.app_state.providers.keys())
        routed_count = {}
        for pref in self.app_state.config.preferences:
            for route in pref.routes:
                routed_count[route.provider] = routed_count.get(route.provider, 0) + 1
        providers = list(self.app_state.providers.keys())
        providers.sort(key=lambda n: (-routed_count.get(n, 0), n))
        return providers

    def _get_provider_keys(self, provider_name: str) -> List[Dict]:
        """Get key info for a provider from config."""
        pm = self.app_state.providers.get(provider_name)
        if not pm or not pm.config:
            return []
        
        keys = []
        key_envs = []
        if pm.config.key_envs:
            key_envs = list(pm.config.key_envs)
        elif pm.config.key_env:
            key_envs = [pm.config.key_env]
        
        for i, kenv in enumerate(key_envs):
            # Get last used from recent events
            last_used = None
            for event in reversed(self.app_state.recent_events[-500:]):
                if event.provider == provider_name and event.key_env == kenv:
                    last_used = event.timestamp
                    break
            keys.append({
                "index": i,
                "key_env": kenv,
                "last_used": last_used,
            })
        return keys

    def _fetch_config(self) -> None:
        """Fetch and reload config."""
        try:
            from routeops.adapters.config_editor import ConfigEditor
            ce = ConfigEditor()
            # Audit catch: ConfigEditor has load(), not load_config() — the
            # refresh key was calling a method that doesn't exist and always
            # failed with AttributeError.
            data = ce.load()
            from routeops.adapters.config_read import ConfigAdapter
            self.app_state.config = ConfigAdapter(str(ce.config_path))._parse_config(data)
            # Update provider configs
            for pm in self.app_state.providers.values():
                for pref in self.app_state.config.preferences:
                    for route in pref.routes:
                        if route.provider == pm.name:
                            # Find matching provider config
                            for pc in self.app_state.config.providers:
                                if pc.name == pm.name:
                                    pm.config = pc
                                    break
                            break
            self.last_fetch = time.time()
        except Exception as e:
            self.message = f"Config reload failed: {e}"
            self.message_color = curses.color_pair(4)

    def _add_key(self, providers: List[str]) -> None:
            """Add new key to selected provider.

            Order: write .env first (atomic, with rollback), then config.json
            (atomic, with rollback). If config fails, the .env key remains but is
            harmless (unused). If .env fails, config is untouched.
            """
            if not providers or not self.new_key_env or not self.new_key_value:
                self.message = "Both key_env name and value required"
                self.message_color = curses.color_pair(4)
                return

            provider_name = providers[self.selected_provider]

            try:
                from routeops.adapters.config_editor import ConfigEditor, EnvEditor
                ce = ConfigEditor()
                ee = EnvEditor()

                # Load current config (plain dict — editable copy)
                config = ce.load()

                # Find provider and add key_env
                for pc in config.get("providers", []):
                    if pc.get("name") == provider_name:
                        if not pc.get("key_envs"):
                            pc["key_envs"] = []
                        # Check if already exists
                        if self.new_key_env not in pc["key_envs"]:
                            # 1. Write .env first (atomic, rollback on failure)
                            ee.set_var(self.new_key_env, self.new_key_value)

                            # 2. Stage config change
                            pc["key_envs"].append(self.new_key_env)

                            # 3. Save config (atomic, rollback on failure)
                            ce.commit(config, f"add key {self.new_key_env} to {provider_name}")

                            # Reload
                            self._fetch_config()
                            self.message = f"Added key {self.new_key_env} to {provider_name}"
                            self.message_color = curses.color_pair(2)
                        else:
                            self.message = f"Key {self.new_key_env} already exists"
                            self.message_color = curses.color_pair(3)
                        break

                self.mode = "list"
                self.new_key_env = ""
                self.new_key_value = ""
            except Exception as e:
                self.message = f"Add key failed: {e}"
                self.message_color = curses.color_pair(4)
                self.mode = "list"

    def _remove_key(self, providers: List[str]) -> None:
            """Remove selected key from provider."""
            provider_name = providers[self.selected_provider]
            pm = self.app_state.providers.get(provider_name)
            if not pm or not pm.config:
                return

            key_envs = []
            if pm.config.key_envs:
                key_envs = list(pm.config.key_envs)
            elif pm.config.key_env:
                key_envs = [pm.config.key_env]

            if self.selected_key >= len(key_envs):
                return

            key_env = key_envs[self.selected_key]

            try:
                from routeops.adapters.config_editor import ConfigEditor
                ce = ConfigEditor()
                config = ce.load()

                for pc in config.get("providers", []):
                    if pc.get("name") == provider_name:
                        pc_key_envs = pc.get("key_envs", [])
                        if key_env in pc_key_envs:
                            # Check if used by any route
                            used = False
                            for pref in config.get("preferences", []):
                                for route in pref.get("routes", []):
                                    if route.get("provider") == provider_name:
                                        used = True
                                        break
                                if used:
                                    break

                            if used and len(pc_key_envs) <= 1:
                                self.message = "Cannot remove last key for routed provider"
                                self.message_color = curses.color_pair(4)
                                self.mode = "list"
                                return

                            pc["key_envs"] = [k for k in pc_key_envs if k != key_env]
                            ce.commit(config, f"remove key {key_env} from {provider_name}")
                            self._fetch_config()
                            self.message = f"Removed key {key_env} from {provider_name}"
                            self.message_color = curses.color_pair(2)
                        break

                self.mode = "list"
                self.show_detail = False
            except Exception as e:
                self.message = f"Remove key failed: {e}"
                self.message_color = curses.color_pair(4)
                self.mode = "list"

    def _write_env_var(self, key_env: str, value: str) -> None:
        """Write key to ~/.hermes/.env via the transactional EnvEditor.

        backup → validate → atomic tmp+rename → rollback on failure.
        Never a bare open(...,'w'): a crash mid-write would destroy every
        provider credential with no recovery path.
        """
        from routeops.adapters.config_editor import EnvEditor
        EnvEditor().set_var(key_env, value)

    def _test_all_keys(self, providers: List[str]) -> None:
        """Test all keys of selected provider."""
        if not providers:
            return
        provider_name = providers[self.selected_provider]
        pm = self.app_state.providers.get(provider_name)
        if not pm:
            return
        
        from routeops.adapters.config_editor import ConfigEditor
        ce = ConfigEditor()
        key_envs = []
        if pm.config:
            if pm.config.key_envs:
                key_envs = list(pm.config.key_envs)
            elif pm.config.key_env:
                key_envs = [pm.config.key_env]
        
        self.test_results = []
        any_ok = False
        for i, kenv in enumerate(key_envs):
            r = ce.probe_catalog(pm.config.base_url, kenv, timeout=15)
            if r.get("ok"):
                self.test_results.append((i, kenv, True, f"{len(r['models'])} models"))
                any_ok = True
            else:
                self.test_results.append((i, kenv, False, r.get('error', '')[:40]))
        
        self.test_verdict = "OK" if any_ok else "ALL KEYS DEAD"
        self.test_provider = provider_name

    test_results = []
    test_verdict = ""
    test_provider = ""

    def render(self, term) -> None:
        """Render key management screen."""
        rows, cols = term.size.rows, term.size.cols
        providers = self._get_sorted_providers()

        if self.mode == "add_key":
            # Add key form
            term.write(0, 0, f"● KEY MANAGEMENT — ADD KEY to {providers[self.selected_provider] if providers else 'N/A'}  [Tab/↑↓]=field  [Enter]=save  [Esc]=cancel", curses.A_BOLD | curses.color_pair(1))
            term.write(1, 0, "─" * (cols - 2), curses.color_pair(1))
            
            y = 3
            term.write(y, 4, "Key Env Name:", curses.color_pair(5))
            attr = curses.A_REVERSE if self.input_field == 0 else 0
            term.write(y, 20, f" {self.new_key_env}" + " " * (cols - 25), attr | curses.color_pair(1))
            y += 1
            term.write(y, 4, "Key Value:", curses.color_pair(5))
            attr = curses.A_REVERSE if self.input_field == 1 else 0
            # Mask the value
            masked = "*" * len(self.new_key_value)
            term.write(y, 20, f" {masked}" + " " * (cols - 25), attr | curses.color_pair(1))
            y += 2
            term.write(y, 4, "Example: NVIDIA_API_KEY_4  /  nvapi-xxxxx", curses.color_pair(8))
            return

        elif self.mode == "confirm_remove":
            provider_name = providers[self.selected_provider] if providers else "N/A"
            pm = self.app_state.providers.get(provider_name)
            key_envs = []
            if pm and pm.config:
                if pm.config.key_envs:
                    key_envs = list(pm.config.key_envs)
                elif pm.config.key_env:
                    key_envs = [pm.config.key_env]
            key_env = key_envs[self.selected_key] if self.selected_key < len(key_envs) else "N/A"
            
            term.write(0, 0, f"● KEY MANAGEMENT — CONFIRM REMOVE  [y]=yes  [n/Esc]=no", curses.A_BOLD | curses.color_pair(4))
            term.write(1, 0, "─" * (cols - 2), curses.color_pair(4))
            term.write(3, 4, f"Remove key: {key_env}", curses.color_pair(3))
            term.write(4, 4, f"From provider: {provider_name}", curses.color_pair(3))
            term.write(6, 4, "This will remove the key from config.json and the router will stop using it.", curses.color_pair(8))
            term.write(7, 4, "The key will NOT be deleted from ~/.hermes/.env (manual cleanup required).", curses.color_pair(8))
            return

        # Title
        if self.show_detail:
            provider = providers[self.selected_provider] if providers else "Unknown"
            title = f"● KEY MANAGEMENT: {provider}  [↑/↓]=key  [t]=test  [d]=delete  [Enter/Esc]=back"
        else:
            title = f"● KEY MANAGEMENT  [{len(providers)} providers]  [↑/↓]=provider  [Enter]=keys  [a]=add key  [t]=test all  [r]=refresh  [q/Esc]=Back"
        term.write(0, 0, title[:cols], curses.A_BOLD | curses.color_pair(1))
        term.write(1, 0, "─" * (cols - 2), curses.color_pair(1))

        y = 2

        # Message banner
        if self.message:
            term.write(y, 2, self.message[:cols-4], self.message_color)
            y += 1

        if self.show_detail and providers:
            # Detail view for selected provider
            provider_name = providers[self.selected_provider]
            pm = self.app_state.providers.get(provider_name)
            keys = self._get_provider_keys(provider_name)
            
            # Provider summary
            term.write(y, 2, f"Provider: {provider_name}", curses.A_BOLD | curses.color_pair(1))
            if pm and pm.config:
                term.write(y, 30, f"URL: {pm.config.base_url}", curses.color_pair(5))
            y += 1
            term.write(y, 0, "─" * (cols - 2), curses.color_pair(1))
            y += 1

            # Test results banner
            if hasattr(self, 'test_results') and self.test_results and self.test_provider == provider_name:
                ok = self.test_verdict == "OK"
                term.write(y, 2, f"KEY TEST {provider_name}: {self.test_verdict}", 
                           (curses.color_pair(2) if ok else curses.color_pair(4)) | curses.A_BOLD)
                y += 1
                for idx, kenv, success, detail in self.test_results:
                    color = curses.color_pair(2) if success else curses.color_pair(4)
                    term.write(y, 4, f"  [{idx}] {kenv:<44} {'●' if success else '✗'} {detail}", color)
                    y += 1

            # Key table
            for i, key in enumerate(keys):
                if y >= rows - 3:
                    break
                is_selected = i == self.selected_key
                
                kenv_short = key["key_env"]
                last_used = key["last_used"]
                if last_used:
                    last_str = last_used.strftime("%H:%M:%S") if hasattr(last_used, 'strftime') else str(last_used)[:8]
                else:
                    last_str = "never"
                
                attr = curses.A_REVERSE if is_selected else 0
                line = f"  [{key['index']}] {kenv_short:<50} last: {last_str}"
                term.write(y, 2, line[:cols-4], attr | curses.color_pair(1))
                y += 1
                
                # Test result for this key
                if hasattr(self, 'test_results') and self.test_results:
                    for tidx, tkenv, tsuccess, tdetail in self.test_results:
                        if tkenv == key["key_env"]:
                            color = curses.color_pair(2) if tsuccess else curses.color_pair(4)
                            term.write(y, 4, f"      → {tdetail}", color)
                            y += 1
                            break
        else:
            # Provider list view
            for i, provider_name in enumerate(providers):
                if y >= rows - 3:
                    break
                pm = self.app_state.providers.get(provider_name)
                keys = self._get_provider_keys(provider_name)
                
                total = len(keys)
                
                health = pm.health if pm else None
                if health and health.hard_failed:
                    provider_icon = "✗"
                    provider_color = curses.color_pair(4)
                elif health and not health.healthy:
                    provider_icon = "◐"
                    provider_color = curses.color_pair(3)
                elif health and health.healthy:
                    provider_icon = "●"
                    provider_color = curses.color_pair(2)
                else:
                    provider_icon = "◎"
                    provider_color = curses.color_pair(8)
                
                is_selected = i == self.selected_provider
                attr = curses.A_REVERSE | curses.color_pair(1) if is_selected else provider_color
                
                line = f"  {provider_icon} {provider_name:<20} keys: {total}"
                term.write(y, 2, line[:cols-4], attr)
                y += 1

        # Footer
        footer_y = rows - 1
        if self.show_detail:
            term.write(footer_y, 0, "[↑/↓]=key  [t]=test key  [d]=delete  [Enter/Esc]=back  [a]=add key (from list)", curses.color_pair(8))
        else:
            term.write(footer_y, 0, "[↑/↓]=provider  [Enter]=keys  [a]=add key  [t]=test all  [r]=refresh  [q]=Back", curses.color_pair(8))