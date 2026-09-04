"""Base classes and interfaces for AndroRemote C2 plugins."""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import os


@dataclass
class Command:
    """Represents a command registered by a plugin."""
    name: str
    usage: str
    category: str
    description: str
    details: str
    handler: Callable[[List[str], "PluginContext"], Any]
    completer: Optional[Callable[[str, List[str], "PluginContext"], List[str]]] = None


class PluginContext:
    """Execution context provided to plugins to interact with the C2 server."""

    def __init__(
        self,
        console: Any,
        get_active_id: Callable[[], Optional[str]],
        get_clients: Callable[[], Dict[str, Any]],
        send_and_wait_fn: Callable[..., Optional[str]],
        queue_fn: Callable[[str, str], None],
        broadcast_fn: Callable[[str], None],
        alias_tag_fn: Callable[[str], str],
        resolve_tag_fn: Callable[[str], Optional[str]],
        log_fn: Callable[..., None],
        show_result_fn: Callable[[str], None],
        home_dir: str,
    ):
        self._console = console
        self._get_active_id = get_active_id
        self._get_clients = get_clients
        self._send_and_wait = send_and_wait_fn
        self._queue = queue_fn
        self._broadcast = broadcast_fn
        self._alias_tag = alias_tag_fn
        self._resolve_tag = resolve_tag_fn
        self._log = log_fn
        self._show_result = show_result_fn
        self._home_dir = home_dir

    @property
    def console(self) -> Any:
        """Rich Console instance for rich output."""
        return self._console

    @property
    def active_client(self) -> Optional[str]:
        """Currently selected/active client session ID."""
        return self._get_active_id()

    @property
    def clients(self) -> Dict[str, Any]:
        """Snapshot of tracked clients."""
        return self._get_clients()

    def send_and_wait(self, cmd: str, client_id: Optional[str] = None, timeout: int = 75) -> Optional[str]:
        """Send command to agent and wait for result. Uses active_client if client_id not given."""
        return self._send_and_wait(cmd, client_id=client_id, timeout=timeout)

    def queue(self, client_id: str, cmd: str) -> None:
        """Queue command for client without waiting."""
        self._queue(client_id, cmd)

    def broadcast(self, cmd: str) -> None:
        """Broadcast command to all connected clients."""
        self._broadcast(cmd)

    def alias_tag(self, client_id: str) -> str:
        """Get formatted alias or short id for a client."""
        return self._alias_tag(client_id)

    def resolve_tag(self, tag: str) -> Optional[str]:
        """Resolve tag or client id prefix to full client id."""
        return self._resolve_tag(tag)

    def log(self, sym: str, msg: str, style: str = "") -> None:
        """Log event to operator console and disk."""
        self._log(sym, msg, style)

    def show_result(self, result: str) -> None:
        """Format and print an agent result."""
        self._show_result(result)

    def plugin_data_dir(self, plugin_name: str) -> str:
        """Get or create a persistent directory for plugin data."""
        pdir = os.path.join(self._home_dir, "plugins_data", plugin_name)
        os.makedirs(pdir, exist_ok=True)
        return pdir


def command(
    name: str,
    usage: Optional[str] = None,
    category: str = "plugins",
    description: str = "",
    details: str = "",
    completer: Optional[Callable[[str, List[str], PluginContext], List[str]]] = None,
):
    """Decorator to register a method as an operator command."""
    def decorator(func: Callable):
        setattr(func, "_c2_command", {
            "name": name.lstrip("/").lower(),
            "usage": usage or f"/{name}",
            "category": category,
            "description": description,
            "details": details,
            "completer": completer,
        })
        return func
    return decorator


def hook(event_name: str):
    """Decorator to mark a method as an event hook."""
    def decorator(func: Callable):
        setattr(func, "_c2_hook", event_name)
        return func
    return decorator


class Plugin:
    """Base class for AndroRemote C2 plugins."""

    name: str = ""
    version: str = "1.0.0"
    author: str = "Unknown"
    description: str = ""

    def __init__(self, context: PluginContext):
        self.ctx: PluginContext = context
        self.enabled: bool = True
        self._commands: List[Command] = []

    def on_load(self) -> None:
        """Invoked when the plugin is loaded."""
        pass

    def on_unload(self) -> None:
        """Invoked when the plugin is unloaded."""
        pass

    def register_command(
        self,
        name: str,
        handler: Callable[[List[str], PluginContext], Any],
        usage: Optional[str] = None,
        category: str = "plugins",
        description: str = "",
        details: str = "",
        completer: Optional[Callable[[str, List[str], PluginContext], List[str]]] = None,
    ) -> None:
        """Programmatically register a command."""
        cmd = Command(
            name=name.lstrip("/").lower(),
            usage=usage or f"/{name}",
            category=category,
            description=description,
            details=details,
            handler=handler,
            completer=completer,
        )
        self._commands.append(cmd)

    # Standard lifecycle & event hooks
    def on_beacon(self, client_id: str, meta: Dict[str, Any]) -> None:
        """Agent beacon arrived."""
        pass

    def on_client_connect(self, client_id: str, meta: Dict[str, Any]) -> None:
        """New agent connected for the first time."""
        pass

    def on_client_disconnect(self, client_id: str) -> None:
        """Agent timed out or disconnected."""
        pass

    def on_command_queued(self, client_id: str, cmd: str) -> None:
        """A command was queued for dispatch."""
        pass

    def on_result(self, client_id: str, cmd: str, result: str) -> None:
        """An agent returned a result."""
        pass
