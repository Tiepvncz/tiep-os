# Agents

Registry of agents (skills, automations, tools) in tiep-os.

## Active Agents

| Agent | Directory | Description | Schedule |
|-------|-----------|-------------|----------|
| Polymarket Scanner | `skills/polymarket/` | Scans Polymarket for trading opportunities | Daily |
| Polymarket Analyzer | `skills/polymarket/` | Analyzes market data and generates insights | On-demand |

## Adding a New Agent

1. Create a directory under `skills/<agent-name>/`
2. Include a `run.py` (or equivalent entry point)
3. Add a `README.md` describing what the agent does, its inputs/outputs, and dependencies
4. Register it in this file
