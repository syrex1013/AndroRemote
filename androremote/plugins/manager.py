"""Plugin manager for discovering, loading, and supervising AndroRemote C2 plugins."""

import importlib
import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from androremote.plugins.base import Command, Plugin, PluginContext


class PluginManager:
    """Manages the lifecycle of AndroRemote C2 plugins."""

    def __init__(self, context: PluginContext):
        self.ctx: PluginContext = context
        self.plugins: Dict[str, Plugin] = {}
        self.commands: Dict[str, Tuple[Plugin, Command]] = {}
        self.hooks: Dict[str, List[Tuple[Plugin, Callable]]] = {
            "on_beacon": [],
            "on_client_connect": [],
            "on_client_disconnect": [],
            "on_command_queued": [],
            "on_result": [],
        }

    def load_all(self, extra_dirs: Optional[List[str]] = None) -> None:
        """Discover and load plugins from builtins and user directories."""
        # 1. Built-in plugins
        try:
            from androremote.plugins import builtin
            builtin_dir = os.path.dirname(builtin.__file__)
            self.load_directory(builtin_dir, is_builtin=True)
        except Exception as e:
            self.ctx.log("!", f"error locating builtin plugins: {e}", "yellow")

        # 2. User plugins directory ~/.androremote/plugins
        user_dir = os.path.join(self.ctx._home_dir, "plugins")
        if os.path.isdir(user_dir):
            self.load_directory(user_dir, is_builtin=False)
        else:
            try:
                os.makedirs(user_dir, exist_ok=True)
            except OSError:
                pass

        # 3. Extra directories if configured
        if extra_dirs:
            for edir in extra_dirs:
                if os.path.isdir(edir):
                    self.load_directory(edir, is_builtin=False)

    def load_directory(self, dir_path: str, is_builtin: bool = False) -> None:
        """Scan a directory for python files or packages and load plugins."""
        if not os.path.isdir(dir_path):
            return

        for entry in os.listdir(dir_path):
            if entry.startswith((".", "_")) and not entry.endswith(".py"):
                continue
            full_path = os.path.join(dir_path, entry)
            if entry.endswith(".py") and entry != "__init__.py":
                mod_name = entry[:-3]
                self.load_from_file(full_path, mod_name=mod_name)
            elif os.path.isdir(full_path) and os.path.isfile(os.path.join(full_path, "__init__.py")):
                self.load_from_file(os.path.join(full_path, "__init__.py"), mod_name=entry)

    def load_from_file(self, file_path: str, mod_name: Optional[str] = None) -> Optional[Plugin]:
        """Load a plugin from a specific file path."""
        file_path = os.path.abspath(file_path)
        if not os.path.isfile(file_path):
            self.ctx.log("!", f"plugin file not found: {file_path}", "yellow")
            return None

        if not mod_name:
            mod_name = Path(file_path).stem

        unique_mod_name = f"androremote_plugin_{mod_name}_{abs(hash(file_path))}"

        try:
            spec = importlib.util.spec_from_file_location(unique_mod_name, file_path)
            if not spec or not spec.loader:
                raise ImportError(f"failed to load spec for {file_path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[unique_mod_name] = module
            spec.loader.exec_module(module)

            plugin_instance = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    inspect.isclass(attr)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                ):
                    inst = attr(self.ctx)
                    pname = inst.name or mod_name
                    inst.name = pname
                    self._register_plugin(inst)
                    plugin_instance = inst
                    break

            if not plugin_instance:
                self.ctx.log("!", f"no Plugin subclass found in {file_path}", "yellow")
                return None

            return plugin_instance

        except Exception as e:
            self.ctx.log("!", f"failed loading plugin {mod_name} ({file_path}): {e}", "red")
            return None

    def _register_plugin(self, plugin: Plugin) -> None:
        """Register a plugin instance, inspecting its commands and hooks."""
        # Unload previous instance if reloading
        if plugin.name in self.plugins:
            self.unload_plugin(plugin.name)

        # Call on_load
        try:
            plugin.on_load()
        except Exception as e:
            self.ctx.log("!", f"plugin '{plugin.name}' on_load error: {e}", "red")

        # Gather commands from decorator
        for _, member in inspect.getmembers(plugin):
            if hasattr(member, "_c2_command"):
                info = getattr(member, "_c2_command")
                cmd = Command(
                    name=info["name"],
                    usage=info["usage"],
                    category=info["category"],
                    description=info["description"],
                    details=info["details"],
                    handler=member,
                    completer=info["completer"],
                )
                plugin._commands.append(cmd)

        # Register commands to manager
        for cmd in plugin._commands:
            self.commands[cmd.name.lower()] = (plugin, cmd)

        # Gather hooks
        for hook_name in list(self.hooks.keys()):
            # Method override (e.g. on_beacon)
            method = getattr(plugin, hook_name, None)
            if callable(method) and getattr(Plugin, hook_name, None) is not getattr(plugin.__class__, hook_name, None):
                self.hooks[hook_name].append((plugin, method))

        # Decorated hooks
        for _, member in inspect.getmembers(plugin):
            if hasattr(member, "_c2_hook"):
                hname = getattr(member, "_c2_hook")
                self.hooks.setdefault(hname, []).append((plugin, member))

        self.plugins[plugin.name] = plugin
        cmd_names = [c.name for c in plugin._commands]
        c_str = f" [dim](commands: {', '.join(cmd_names)})[/dim]" if cmd_names else ""
        self.ctx.log("⚡", f"plugin [bold cyan]{plugin.name}[/bold cyan] v{plugin.version} loaded{c_str}", "green")

    def unload_plugin(self, name: str) -> bool:
        """Unload and clean up a plugin."""
        plugin = self.plugins.get(name)
        if not plugin:
            return False

        try:
            plugin.on_unload()
        except Exception as e:
            self.ctx.log("!", f"plugin '{name}' on_unload error: {e}", "yellow")

        # Remove commands
        to_del = [cname for cname, (p, _) in self.commands.items() if p == plugin]
        for cname in to_del:
            self.commands.pop(cname, None)

        # Remove hooks
        for hook_name, handlers in self.hooks.items():
            self.hooks[hook_name] = [(p, h) for p, h in handlers if p != plugin]

        self.plugins.pop(name, None)
        self.ctx.log("⚡", f"plugin [bold yellow]{name}[/bold yellow] unloaded", "yellow")
        return True

    def reload_plugin(self, name: str) -> bool:
        """Reload an active plugin."""
        plugin = self.plugins.get(name)
        if not plugin:
            # Check if it exists in user plugins dir or builtins
            user_path = os.path.join(self.ctx._home_dir, "plugins", f"{name}.py")
            if os.path.isfile(user_path):
                return self.load_from_file(user_path, mod_name=name) is not None
            return False

        # Attempt to find source file
        try:
            src_file = inspect.getfile(plugin.__class__)
            self.unload_plugin(name)
            return self.load_from_file(src_file, mod_name=name) is not None
        except Exception as e:
            self.ctx.log("!", f"failed to reload '{name}': {e}", "red")
            return False

    def has_command(self, name: str) -> bool:
        """Check if an operator command is registered by a plugin."""
        return name.lower() in self.commands

    def dispatch(self, op: str, argv: List[str]) -> bool:
        """Execute a plugin command."""
        op_clean = op.lstrip("/").lower()
        entry = self.commands.get(op_clean)
        if not entry:
            return False

        plugin, cmd = entry
        if not plugin.enabled:
            self.ctx.log("!", f"plugin '{plugin.name}' is currently disabled", "yellow")
            return True

        try:
            cmd.handler(argv, self.ctx)
        except Exception as e:
            self.ctx.log("!", f"plugin command '/{op_clean}' failed: {e}", "red")
            self.ctx.console.print_exception(show_locals=False)
        return True

    def trigger_hook(self, event_name: str, *args, **kwargs) -> None:
        """Dispatch an event hook to all listening plugins."""
        handlers = self.hooks.get(event_name, [])
        for plugin, handler in handlers:
            if not plugin.enabled:
                continue
            try:
                handler(*args, **kwargs)
            except Exception as e:
                self.ctx.log("!", f"plugin '{plugin.name}' hook '{event_name}' error: {e}", "yellow")

    def get_completions(self, text: str, tokens: List[str]) -> List[str]:
        """Compute tab completions for plugin commands."""
        if not tokens:
            return []

        cmd_name = tokens[0].lstrip("/").lower()
        entry = self.commands.get(cmd_name)
        if entry:
            _, cmd = entry
            if cmd.completer:
                try:
                    return cmd.completer(text, tokens, self.ctx)
                except Exception:
                    return []
        return []
