"""
Claude Opus 4.8 agent loop with tool use.

Implements a conversational REPL that maintains message history (last 20 turns)
and handles multi-step tool use rounds automatically.
"""
import json
import sys
import urllib.error
import urllib.request
from typing import Any

from ffootball.agent.prompts import build_system_prompt
from ffootball.agent.tools import TOOL_DEFINITIONS, ToolHandler
from ffootball.config import Config
from ffootball.db.queries import get_config_value
from ffootball.db.schema import get_connection

MODEL = "claude-opus-4-8"
MAX_TOKENS = 4096
MAX_HISTORY_TURNS = 20  # keep last 20 user+assistant pairs


# ---------------------------------------------------------------------------
# Anthropic API (raw HTTP — no SDK dependency)
# ---------------------------------------------------------------------------

def _call_anthropic(
    api_key: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
        "tools": tools,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Anthropic API {e.code}: {body[:400]}") from e


# ---------------------------------------------------------------------------
# Terminal helpers (no rich dependency)
# ---------------------------------------------------------------------------

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _print_banner() -> None:
    print(f"\n{BOLD}{CYAN}╔══════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}{CYAN}║   FFootball Dynasty Agent                ║{RESET}")
    print(f"{BOLD}{CYAN}║   Powered by Claude Opus 4.8             ║{RESET}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════╝{RESET}\n")
    print(f"{DIM}Type your question or use /help for commands. /quit to exit.{RESET}\n")


def _print_agent(text: str) -> None:
    print(f"\n{GREEN}{BOLD}Agent:{RESET} {text}\n")


def _print_tool_call(name: str) -> None:
    print(f"{DIM}  → {name}(){RESET}", flush=True)


def _print_error(msg: str) -> None:
    print(f"\n{YELLOW}Error:{RESET} {msg}\n")


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------

def _handle_slash(cmd: str, cfg: Config, handler: ToolHandler) -> bool:
    """Handle slash commands. Returns True if handled, False if unknown."""
    parts = cmd.strip().split(None, 2)
    command = parts[0].lower()

    if command == "/quit" or command == "/exit":
        print(f"\n{CYAN}Goodbye.{RESET}\n")
        sys.exit(0)

    elif command == "/help":
        print(f"""
{BOLD}Available commands:{RESET}
  /sync [scope]        Sync from Sleeper API (full|league|players|roster|matchups|transactions)
  /journal list        Show recent decision journal entries
  /journal add         Interactively add a journal entry
  /journal grade <id>  Grade a past journal entry
  /help                Show this help
  /quit                Exit
""")
        return True

    elif command == "/sync":
        scope = parts[1] if len(parts) > 1 else "full"
        print(f"\nSyncing ({scope})...")
        result = json.loads(handler.dispatch("sync_data", {"scope": scope}))
        print(f"Done: {result.get('results', result)}\n")
        return True

    elif command == "/journal":
        sub = parts[1].lower() if len(parts) > 1 else "list"
        if sub == "list":
            _journal_list(cfg)
        elif sub == "add":
            _journal_add(cfg, handler)
        elif sub == "grade" and len(parts) > 2:
            try:
                entry_id = int(parts[2])
                _journal_grade(cfg, handler, entry_id)
            except ValueError:
                print("Usage: /journal grade <id>")
        else:
            print("Usage: /journal list | /journal add | /journal grade <id>")
        return True

    return False


def _journal_list(cfg: Config) -> None:
    conn = get_connection(cfg.db_path)
    rows = conn.execute(
        """
        SELECT id, created_at, decision_type, title, outcome_grade
        FROM decision_journal
        ORDER BY id DESC LIMIT 20
        """
    ).fetchall()
    conn.close()
    if not rows:
        print("\nNo journal entries yet.\n")
        return
    print(f"\n{BOLD}Decision Journal (last 20):{RESET}")
    print(f"{'ID':>4}  {'Date':10}  {'Type':12}  {'Grade':5}  Title")
    print("-" * 70)
    for row in rows:
        date = (row["created_at"] or "")[:10]
        grade = row["outcome_grade"] or "-"
        print(f"{row['id']:>4}  {date:10}  {row['decision_type']:12}  {grade:5}  {row['title']}")
    print()


def _journal_add(cfg: Config, handler: ToolHandler) -> None:
    print(f"\n{BOLD}Add Journal Entry{RESET}")
    dtypes = ["trade", "waiver", "drop", "lineup", "cut", "other"]
    dtype = input(f"Decision type ({'/'.join(dtypes)}): ").strip() or "other"
    title = input("Short title: ").strip()
    if not title:
        print("Cancelled.")
        return
    decision_made = input("What did you decide/do: ").strip()
    reasoning = input("Why (reasoning): ").strip()
    context = input("Context (optional): ").strip()
    tags_raw = input("Tags (comma-separated, optional): ").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    conn = get_connection(cfg.db_path)
    row = conn.execute("SELECT value FROM config WHERE key='my_roster_id'").fetchone()
    conn.close()
    season = cfg.sleeper_season

    result = json.loads(handler.dispatch("add_journal_entry", {
        "decision_type": dtype,
        "title": title,
        "context": context or None,
        "decision_made": decision_made,
        "reasoning": reasoning or None,
        "season": season,
        "tags": tags or None,
    }))
    if result.get("created"):
        print(f"\n{GREEN}✓ Journal entry #{result['id']} recorded.{RESET}\n")
    else:
        print(f"\n{YELLOW}Failed: {result.get('error')}{RESET}\n")


def _journal_grade(cfg: Config, handler: ToolHandler, entry_id: int) -> None:
    conn = get_connection(cfg.db_path)
    row = conn.execute(
        "SELECT title, decision_made, outcome_grade FROM decision_journal WHERE id=?", (entry_id,)
    ).fetchone()
    conn.close()
    if not row:
        print(f"No entry with ID {entry_id}.")
        return
    print(f"\n{BOLD}Grading entry #{entry_id}:{RESET} {row['title']}")
    print(f"Decision: {row['decision_made']}")
    print(f"Current grade: {row['outcome_grade'] or 'ungraded'}")
    grade = input("New grade (A/B/C/D/F): ").strip().upper()
    if grade not in ("A", "B", "C", "D", "F"):
        print("Invalid grade. Cancelled.")
        return
    notes = input("Outcome notes: ").strip()
    result = json.loads(handler.dispatch("grade_journal_entry", {
        "id": entry_id, "grade": grade, "outcome_notes": notes
    }))
    if result.get("graded"):
        print(f"\n{GREEN}✓ Entry #{entry_id} graded {grade}.{RESET}\n")
    else:
        print(f"\n{YELLOW}Failed: {result.get('error')}{RESET}\n")


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

def _load_startup_context(cfg: Config) -> tuple[str, str]:
    """Load league settings and strategy doc for system prompt injection."""
    league_ctx = ""
    strategy_ctx = ""

    try:
        from ffootball.agent.tools import ToolHandler
        handler = ToolHandler(cfg)
        settings_raw = handler.dispatch("get_league_settings", {})
        settings = json.loads(settings_raw)
        if "error" not in settings:
            name = settings.get("name", "")
            scoring = settings.get("scoring_settings", {})
            roster_pos = settings.get("roster_positions", [])
            league_ctx = (
                f"League: {name}\n"
                f"Roster positions: {', '.join(roster_pos)}\n"
                f"Taxi: {settings.get('taxi_slots',0)} | IR: {settings.get('ir_slots',0)}\n"
                f"Trade deadline: week {settings.get('trade_deadline',0)}\n"
                f"Scoring: {'half-PPR' if scoring.get('rec',0)==0.5 else 'PPR' if scoring.get('rec',1)==1 else 'standard'}"
            )
    except Exception:
        pass

    strategy_path = cfg.context_dir / "league_strategy.md"
    if strategy_path.exists():
        strategy_ctx = strategy_path.read_text()[:2000]  # cap at 2000 chars

    return league_ctx, strategy_ctx


def run_chat(cfg: Config) -> None:
    _print_banner()

    handler = ToolHandler(cfg)
    league_ctx, strategy_ctx = _load_startup_context(cfg)
    system_prompt = build_system_prompt(cfg, league_ctx, strategy_ctx)

    messages: list[dict] = []

    while True:
        try:
            user_input = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{CYAN}Goodbye.{RESET}\n")
            break

        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            _handle_slash(user_input, cfg, handler)
            continue

        # Add user message
        messages.append({"role": "user", "content": user_input})

        # Trim history to last MAX_HISTORY_TURNS pairs
        if len(messages) > MAX_HISTORY_TURNS * 2:
            messages = messages[-(MAX_HISTORY_TURNS * 2):]

        # Agent loop — keep going until stop_reason is "end_turn"
        while True:
            try:
                response = _call_anthropic(
                    api_key=cfg.anthropic_api_key,
                    system=system_prompt,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                )
            except RuntimeError as e:
                _print_error(str(e))
                messages.pop()  # remove the failed user message
                break

            stop_reason = response.get("stop_reason")
            content_blocks = response.get("content", [])

            # Collect text and tool_use blocks
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            for block in content_blocks:
                if block.get("type") == "text":
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append(block)

            # Print any text response
            if text_parts:
                _print_agent("\n".join(text_parts))

            # Add assistant turn to history
            messages.append({"role": "assistant", "content": content_blocks})

            # If no tool calls or stop_reason is end_turn, we're done
            if stop_reason == "end_turn" or not tool_calls:
                break

            # Execute tool calls and add results
            tool_results: list[dict] = []
            for tc in tool_calls:
                tool_name = tc.get("name", "")
                tool_input = tc.get("input", {})
                tool_id = tc.get("id", "")
                _print_tool_call(tool_name)
                result_str = handler.dispatch(tool_name, tool_input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_str,
                })

            messages.append({"role": "user", "content": tool_results})
