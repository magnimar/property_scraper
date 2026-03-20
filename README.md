# Property scraper

Scrapes [Fasteignir.is](https://fasteignir.visir.is) listings for configured users and can email summaries (Brevo).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### `config.json` (required shape)

`config.json` must be a **JSON array** of user objects (not an object keyed by name). Each object must include a string **`"user"`** id (used with `--user`) plus the same fields as before (`BREVO_API_KEY`, `FROM_EMAIL`, `TO_EMAIL`, `MIN_PRICE`, `MAX_PRICE`, `MIN_BEDROOMS`, `MAX_BEDROOMS`, `ZIP_CODES`, optional `ignored_strings`, etc.).

If the file is not an array, or is empty, or any entry is invalid, the program **exits with an error**.

Copy `config.example.json` to `config.json` and fill in real values (`config.json` is gitignored).

**`--schedule`** runs users in **array order** (first object first, then the next, …).

Optional: a **`.env`** file in the project root for schedule mode (see below).

---

## Usage

### One-off run (single user)

Runs the scrape + filters + email once for the given user id:

```bash
python scraper.py --user magni
python scraper.py --user gabriela
```

`--user` must match the **`"user"`** field of exactly one object in the `config.json` array.

### Scheduled runs (daemon)

Runs forever: **once per calendar day** when the clock matches `SCRAPER_HOUR`/`SCRAPER_MINUTE`, it reloads `config.json` and runs **`Scraper` for every object in the array**, in order, with **60 seconds** between users.

```bash
python scraper.py --schedule
```

**Do not** pass `--user` together with `--schedule`.

**Environment variables** (e.g. in `.env`; `python-dotenv` loads them automatically in schedule mode):

| Variable | Required | Description |
|----------|----------|-------------|
| `SCRAPER_HOUR` | Yes | Hour (0–23), local time |
| `SCRAPER_MINUTE` | Yes | Minute (0–59), local time |

Example `.env`:

```env
SCRAPER_HOUR=22
SCRAPER_MINUTE=0
```

If one user run fails, the loop logs the error and continues with the next user in the list.

### systemd (Raspberry Pi / server)

The sample unit in `service/property_scraper.service` starts:

```text
python -u /opt/property_scraper/scraper.py --schedule
```

Set `WorkingDirectory` to the repo, ensure `.env` with `SCRAPER_HOUR` / `SCRAPER_MINUTE` is present there (or export vars in the unit). Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now property_scraper.service
```

---

## Examples

**Magni**

```bash
python scraper.py --user magni
```

**Gabríela**

```bash
python scraper.py --user gabriela
```

**Daily 22:00 for everyone in `config.json` (in list order)**

```bash
# .env: SCRAPER_HOUR=22 SCRAPER_MINUTE=0
python scraper.py --schedule
```
