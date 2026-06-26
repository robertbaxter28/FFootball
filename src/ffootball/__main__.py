import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


@click.group()
def cli():
    """FFootball — Dynasty fantasy football agent powered by Claude."""


@cli.command()
def setup():
    """Interactive first-run setup: configure .env and initialize the database."""
    from ffootball.config import ENV_PATH, write_env
    from ffootball.db.schema import init_db, verify_schema

    console.print(Panel.fit(
        "[bold cyan]FFootball Setup[/bold cyan]\n"
        "Configure your Sleeper connection and initialize the local database.",
        border_style="cyan",
    ))

    values = {}

    api_key = click.prompt("Anthropic API key", hide_input=True)
    values["ANTHROPIC_API_KEY"] = api_key

    username = click.prompt("Sleeper username")
    values["SLEEPER_USERNAME"] = username

    league_id = click.prompt("Sleeper league ID (from app URL)")
    values["SLEEPER_LEAGUE_ID"] = league_id

    season = click.prompt("Current NFL season year", default="2025")
    values["SLEEPER_SEASON"] = season

    write_env(values)
    console.print(f"[green]✓[/green] Wrote config to [bold]{ENV_PATH}[/bold]")

    from ffootball.config import load_config
    cfg = load_config()
    init_db(cfg.db_path)
    console.print(f"[green]✓[/green] Initialized database at [bold]{cfg.db_path}[/bold]")

    # Resolve user_id from Sleeper and store it
    console.print("Fetching your Sleeper user ID...")
    try:
        from ffootball.sleeper.client import get_user, SleeperAPIError
        from ffootball.db.schema import get_connection
        from ffootball.db.queries import set_config_value
        user = get_user(cfg.sleeper_username)
        user_id = user.get("user_id", "")
        if user_id:
            conn = get_connection(cfg.db_path)
            with conn:
                set_config_value(conn, "user_id", user_id)
                set_config_value(conn, "sleeper_username", cfg.sleeper_username)
                set_config_value(conn, "league_id", cfg.sleeper_league_id)
                set_config_value(conn, "current_season", cfg.sleeper_season)
            conn.close()
            console.print(f"[green]✓[/green] User ID: [bold]{user_id}[/bold]")
        else:
            console.print("[yellow]⚠[/yellow]  Could not resolve user_id — check your username")
    except Exception as e:
        console.print(f"[yellow]⚠[/yellow]  Could not reach Sleeper API: {e}")
        console.print("  (This is fine — run [cyan]ffootball sync[/cyan] once you have network access)")

    results = verify_schema(cfg.db_path)
    table = Table(title="Database Tables", show_header=True)
    table.add_column("Table")
    table.add_column("Status")
    for tname, ok in results.items():
        status = "[green]✓ ready[/green]" if ok else "[red]✗ missing[/red]"
        table.add_row(tname, status)
    console.print(table)

    all_ok = all(results.values())
    if all_ok:
        console.print("\n[bold green]Setup complete.[/bold green] Run [cyan]ffootball sync[/cyan] to pull your league data.")
    else:
        console.print("\n[bold red]Some tables are missing.[/bold red] Try running setup again.")


@cli.command()
@click.option("--scope", default="full", type=click.Choice(["full", "roster", "matchups", "transactions", "players"]))
def sync(scope):
    """Pull fresh data from Sleeper API into the local database."""
    from ffootball.config import load_config
    cfg = load_config()
    missing = cfg.validate()
    if missing:
        console.print(f"[red]Missing config keys:[/red] {', '.join(missing)}")
        console.print("Run [cyan]ffootball setup[/cyan] first.")
        raise SystemExit(1)

    from ffootball.sync.engine import SyncEngine
    from ffootball.db.schema import get_connection
    from ffootball.db.queries import set_config_value

    # Ensure user_id is set (needed for roster sync)
    conn = get_connection(cfg.db_path)
    from ffootball.db.queries import get_config_value
    user_id = get_config_value(conn, "user_id")
    conn.close()

    if not user_id:
        console.print("Resolving Sleeper user ID...")
        try:
            from ffootball.sleeper.client import get_user
            user = get_user(cfg.sleeper_username)
            uid = user.get("user_id", "")
            if uid:
                conn = get_connection(cfg.db_path)
                with conn:
                    set_config_value(conn, "user_id", uid)
                    set_config_value(conn, "sleeper_username", cfg.sleeper_username)
                    set_config_value(conn, "league_id", cfg.sleeper_league_id)
                    set_config_value(conn, "current_season", cfg.sleeper_season)
                conn.close()
                console.print(f"[green]✓[/green] user_id: {uid}")
        except Exception as e:
            console.print(f"[red]Failed to resolve user_id:[/red] {e}")
            raise SystemExit(1)

    engine = SyncEngine(cfg)
    try:
        results = engine.sync(scope=scope)
    finally:
        engine.close()

    table = Table(title=f"Sync Results ({scope})", show_header=True)
    table.add_column("Scope")
    table.add_column("Records", justify="right")
    for k, v in results.items():
        table.add_row(k, str(v))
    console.print(table)
    console.print("[bold green]Sync complete.[/bold green]")


@cli.command()
def chat():
    """Start an interactive chat session with your dynasty GM agent."""
    from ffootball.config import load_config
    cfg = load_config()
    missing = cfg.validate()
    if missing:
        console.print(f"[red]Missing config keys:[/red] {', '.join(missing)}")
        console.print("Run [cyan]ffootball setup[/cyan] first.")
        raise SystemExit(1)

    from ffootball.agent.agent import run_chat
    run_chat(cfg)


@cli.command()
def doctor():
    """Health check: verify config, database, and API connectivity."""
    from ffootball.config import load_config, ENV_PATH
    from ffootball.db.schema import verify_schema

    console.print(Panel.fit("[bold cyan]FFootball Doctor[/bold cyan]", border_style="cyan"))
    ok = True

    # .env check
    if ENV_PATH.exists():
        console.print("[green]✓[/green] .env file found")
    else:
        console.print("[red]✗[/red] .env file missing — run [cyan]ffootball setup[/cyan]")
        ok = False

    cfg = load_config()
    missing = cfg.validate()
    if missing:
        console.print(f"[red]✗[/red] Missing keys: {', '.join(missing)}")
        ok = False
    else:
        console.print("[green]✓[/green] All required config keys present")

    # DB check
    if cfg.db_path.exists():
        results = verify_schema(cfg.db_path)
        missing_tables = [t for t, found in results.items() if not found]
        if missing_tables:
            console.print(f"[red]✗[/red] Missing tables: {', '.join(missing_tables)}")
            ok = False
        else:
            console.print(f"[green]✓[/green] Database OK ({len(results)} tables)")
    else:
        console.print(f"[red]✗[/red] Database not found at {cfg.db_path} — run [cyan]ffootball setup[/cyan]")
        ok = False

    # Sync age check
    if cfg.db_path.exists():
        from ffootball.db.schema import get_connection
        conn = get_connection(cfg.db_path)
        row = conn.execute("SELECT value FROM config WHERE key='last_sync_full'").fetchone()
        conn.close()
        if row:
            from datetime import datetime, timezone
            last = datetime.fromisoformat(row["value"])
            age_hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if age_hours > 48:
                console.print(f"[yellow]⚠[/yellow]  Last full sync was {age_hours:.0f}h ago — consider running [cyan]ffootball sync[/cyan]")
            else:
                console.print(f"[green]✓[/green] Last full sync: {age_hours:.1f}h ago")
        else:
            console.print("[yellow]⚠[/yellow]  No sync recorded yet — run [cyan]ffootball sync[/cyan]")

    # API connectivity check
    console.print("\nChecking Sleeper API...", end=" ")
    try:
        import httpx
        r = httpx.get("https://api.sleeper.app/v1/user/sleeperbot", timeout=5)
        if r.status_code == 200:
            console.print("[green]✓ reachable[/green]")
        else:
            console.print(f"[yellow]HTTP {r.status_code}[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ {e}[/red]")
        ok = False

    console.print()
    if ok:
        console.print("[bold green]All checks passed.[/bold green]")
    else:
        console.print("[bold red]Some checks failed.[/bold red] See above for details.")


if __name__ == "__main__":
    cli()
