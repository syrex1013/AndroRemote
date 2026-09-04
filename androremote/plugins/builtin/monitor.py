"""Monitor plugin: session telemetry, beacon rate tracking, and event monitoring."""

import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List
from rich.table import Table
from rich import box
from rich.markup import escape

from androremote.plugins.base import Plugin, command, PluginContext


class MonitorPlugin(Plugin):
    name = "monitor"
    version = "1.0.0"
    author = "AndroRemote"
    description = "Session telemetry and beacon event monitoring"

    def __init__(self, context: PluginContext):
        super().__init__(context)
        self.beacons: Dict[str, deque] = {}  # cid -> deque of timestamps
        self.events: deque = deque(maxlen=100)  # recent events

    def on_client_connect(self, client_id: str, meta: Dict[str, Any]) -> None:
        ip = meta.get("ip", "unknown")
        model = meta.get("model", "unknown")
        ts = datetime.now().strftime("%H:%M:%S")
        self.events.append((ts, "CONNECT", client_id, f"model={model} ip={ip}"))

    def on_beacon(self, client_id: str, meta: Dict[str, Any]) -> None:
        now = time.time()
        if client_id not in self.beacons:
            self.beacons[client_id] = deque(maxlen=20)
        self.beacons[client_id].append(now)

    def on_result(self, client_id: str, cmd: str, result: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        status = "OK" if result.startswith(("OK", "PONG")) else ("ERR" if result.startswith("ERR") else "DATA")
        self.events.append((ts, "RESULT", client_id, f"{cmd[:30]} -> {status} ({len(result)} bytes)"))

    @command(
        name="monitor",
        usage="/monitor [status|history|clear]",
        category="recon",
        description="View session telemetry and beacon frequency",
        details="Shows average beacon intervals, connected devices telemetry, and event history.",
    )
    def cmd_monitor(self, args: List[str], ctx: PluginContext) -> None:
        sub = args[0].lower() if args else "status"
        console = ctx.console

        if sub == "clear":
            self.events.clear()
            for b in self.beacons.values():
                b.clear()
            ctx.log("✓", "monitor telemetry cleared", "green")
            return

        if sub == "history":
            table = Table(
                title="[bold cyan]RECENT TELEMETRY EVENTS[/bold cyan]",
                box=box.ROUNDED,
                border_style="cyan",
                header_style="bold magenta",
                expand=True,
            )
            table.add_column("Time", style="dim", width=10)
            table.add_column("Type", style="bold yellow", width=10)
            table.add_column("Session", style="cyan", width=18)
            table.add_column("Details", style="white")

            if not self.events:
                table.add_row("-", "-", "-", "[dim]No events logged yet[/dim]")
            else:
                for ts, etype, cid, details in list(self.events)[-25:]:
                    tag = ctx.alias_tag(cid)
                    table.add_row(ts, etype, escape(tag), escape(details))
            console.print(table)
            return

        # Default: status table
        table = Table(
            title="[bold cyan]BEACON & SESSION TELEMETRY[/bold cyan]",
            box=box.ROUNDED,
            border_style="cyan",
            header_style="bold magenta",
            expand=True,
        )
        table.add_column("Session", style="bold cyan", width=20)
        table.add_column("Model", style="white", width=22)
        table.add_column("Last Seen", style="dim", width=12)
        table.add_column("Beacons", style="green", width=10)
        table.add_column("Avg Interval", style="magenta", width=14)

        clients = ctx.clients
        if not clients:
            table.add_row("-", "-", "-", "-", "[dim]No clients connected[/dim]")
        else:
            for cid, c in clients.items():
                tag = ctx.alias_tag(cid)
                model = c.get("model") or "unknown"
                last_seen = c.get("last_seen", 0)
                ago = f"{int(time.time() - last_seen)}s ago" if last_seen else "never"
                history = self.beacons.get(cid, deque())
                count = str(len(history))
                if len(history) >= 2:
                    diffs = [history[i] - history[i - 1] for i in range(1, len(history))]
                    avg = f"{sum(diffs) / len(diffs):.1f}s"
                else:
                    avg = "n/a"
                table.add_row(escape(tag), escape(model), ago, count, avg)

        console.print(table)
