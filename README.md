# Ian's Dashboard

An auto-discovering service dashboard. `generate.py` scans the services it knows about and rewrites the static page, so new tiles appear without editing HTML by hand.

## Running

```bash
python3 generate.py
```

Then serve the generated `www/` directory with any static server.
