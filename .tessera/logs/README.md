# Tessera Event Logs

Structured event logs from Tessera sessions live here as `<session-id>.jsonl`.
The suggestion-gate recorder (`scripts/gate/emit.py`) appends `suggestion_gate`
events here. This directory is gitignored — logs are session-local.
