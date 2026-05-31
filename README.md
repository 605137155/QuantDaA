# QuantDaA
zmh用于大A日线及分时线的选股器

Minute-level A-share hot-stock monitoring scaffold focused on:

- dynamic top-100-by-turnover monitoring
- extensible strategy evaluation
- signal dedupe and watchlist persistence
- desktop-alert-ready application flow

## Quick start

1. Install Python 3.11+.
2. Optionally install `akshare` for live data:

```bash
pip install akshare
```

3. Run the demo mode once:

```bash
python app.py --once
```

4. Run tests:

```bash
python -m unittest discover -s tests
```

## Notes

- If `akshare` is not installed, the app falls back to mock data so the pipeline remains runnable.
- Notifications currently use a console notifier by default; the notifier interface is isolated so a Windows toast implementation can be added later without changing strategy code.
