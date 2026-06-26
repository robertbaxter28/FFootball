# FFootball — Dynasty Fantasy Football Agent

A conversational CLI agent for dynasty fantasy football. Talk to a Claude Opus 4.8 powered GM advisor about your Sleeper league: trade analysis, keep/trade/cut recommendations, waiver wire, long-term roster planning, and a decision journal that holds you accountable over time.

---

## Setup (5 minutes)

### Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or pip
- A [Sleeper](https://sleeper.com) dynasty league
- An [Anthropic API key](https://console.anthropic.com)

### Install

```bash
git clone https://github.com/robertbaxter28/ffootball.git
cd ffootball
uv sync          # installs all dependencies
```

### First-run setup

```bash
uv run ffootball setup
```

This prompts for your Anthropic API key, Sleeper username, league ID, and season year.
It writes a `.env` file and initializes the local SQLite database (`data/dynasty.db`).

**Your league ID** is the numeric ID in your Sleeper league URL:
`https://sleeper.com/leagues/123456789` → league ID is `123456789`

### Sync your data

```bash
uv run ffootball sync
```

This pulls your roster, league settings, matchups, transactions, and draft picks from
Sleeper into the local database, then generates context markdown files.

---

## Daily Use

### Start a chat session

```bash
uv run ffootball chat
```

Ask anything:
- *"What does my roster look like?"*
- *"Should I trade Ja'Marr Chase for CeeDee Lamb and a 2026 first?"*
- *"Who are my sell-high candidates right now?"*
- *"What are my positional weaknesses?"*
- *"Show me free agents at RB under 25."*

The agent queries your live database before answering — no hallucinated roster data.

### In-chat commands

| Command | What it does |
|---|---|
| `/sync [scope]` | Pull fresh data from Sleeper (`full`, `roster`, `matchups`, etc.) |
| `/journal list` | Show recent decision journal entries |
| `/journal list trade` | Filter by decision type |
| `/journal list trade 2025` | Filter by type + season |
| `/journal view <id>` | Full detail for one entry |
| `/journal stats` | Grade breakdown and win-rate summary |
| `/journal add` | Guided entry for a new decision |
| `/journal grade <id>` | Retroactively grade a past decision |
| `/help` | Show all commands |
| `/quit` | Exit |

---

## Command Reference

```
uv run ffootball setup     # First-run: configure .env + init DB
uv run ffootball sync      # Pull fresh data from Sleeper
uv run ffootball sync --scope roster   # Partial sync (roster only)
uv run ffootball chat      # Start interactive agent session
uv run ffootball doctor    # Health check: config, DB, API connectivity
```

Sync scopes: `full` | `league` | `players` | `roster` | `matchups` | `transactions`

---

## How It Works

```
Your question
     ↓
Claude Opus 4.8 (via Anthropic API)
     ↓ calls tools as needed
query_database() → dynasty.db (SQLite)
analyze_trade()  → age-curve dynasty scoring
search_journal() → your past decisions
sync_data()      → Sleeper REST API
     ↓
Concrete recommendation
```

The agent keeps the last 20 conversation turns in memory. Important decisions are
stored in the decision journal (`data/dynasty.db`) and persist across sessions.

---

## Data & Privacy

All data stays local:
- `data/dynasty.db` — SQLite database (gitignored)
- `context/*.md` — Generated markdown snapshots (gitignored)
- `data/agent.log` — Debug log (gitignored)
- `.env` — Your API key (gitignored, never committed)

The only external calls are:
- Sleeper public API (read-only, no auth required)
- Anthropic API (your conversation + tool calls)

---

## League Strategy File

After your first sync, `context/league_strategy.md` is created. This is a hand-maintained
file — the agent reads it at the start of every session. Edit it to capture:
- Your rebuild/contend window
- Which positions you're targeting
- Specific trade rules you follow
- Notes on your league's tendencies

---

## Troubleshooting

**`ffootball setup` can't find your league ID**
Run `uv run ffootball sync` — if Sleeper API is reachable, it will pull your league data.

**`ffootball doctor` shows red**
Run `uv run ffootball doctor` to see which check failed. Most issues are fixed by
re-running `setup` or `sync`.

**Agent says "no data yet"**
Run `uv run ffootball sync` first. The agent reads from the local DB — it has no live
data until you sync.

**Players sync is slow (~30s)**
The Sleeper player universe is ~4MB. It's cached and skipped if synced within 20 hours.

---

## Development

```bash
uv run pytest tests/ -v     # run tests
uv run python -m ffootball  # alt entry point
```

See [CLAUDE.md](CLAUDE.md) for architecture details and how to extend the project.
