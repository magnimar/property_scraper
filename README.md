# Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python scraper.py --user magni
python scraper.py --user gabriela
```

# Environment Variables

Create a `.env` file in the root of the project to set environment variables.

- `SCRAPER_HOUR`: The hour of the day to run the scraper. Defaults to 22.

Example `.env` file:

```
SCRAPER_HOUR=20
```

# Magni

```bash
python scraper.py --user magni
```

# Gabríela

```bash
python scraper.py --user gabriela
```
