# V1 Notes

This scaffold implements:

- configuration loading
- SQLite schema initialization
- provider abstraction with AKShare or mock fallback
- turnover-based monitor pool and focus pool computation
- unified strategy interface
- double-bottom strategy and a simple momentum probe
- signal dedupe, signal persistence, and watchlist persistence
- scheduler entrypoints for one-shot or repeated execution

This repository intentionally keeps UI light in V1 and isolates notifications behind `src/ui/notifier.py`.
