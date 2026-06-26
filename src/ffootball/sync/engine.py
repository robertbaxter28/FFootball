"""Sync engine — Phase 3 implementation placeholder."""
from rich.console import Console

console = Console()


class SyncEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def sync(self, scope: str = "full") -> None:
        console.print(f"[yellow]Sync engine not yet implemented (Phase 3). Scope: {scope}[/yellow]")
        console.print("Coming in Phase 3.")
