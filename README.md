# Run

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python scraper.py
```

## Configuration

The `scraper.py` script uses a `config.json` file to manage certain settings, such as a list of address substrings to ignore. This file is intentionally excluded from version control via `.gitignore` to allow for local customization without affecting the repository.

### `config.json` Structure

The `config.json` file should be located in the root directory of the project and have the following structure:

```json
{
    "ignored_strings": [
        "substring1",
        "substring2",
        "another substring to ignore"
    ]
}
```

-   **`ignored_strings`**: A list of strings. If any of these strings (case-insensitive) are found in a property's address, that property will be skipped during scraping.

### How to Customize

1.  **Create `config.json`**: If it doesn't already exist, create a file named `config.json` in the root directory of the project.
2.  **Edit `ignored_strings`**: Open `config.json` and modify the `ignored_strings` array to include any address substrings you wish to exclude from the search results.
3.  **Run the Scraper**: Execute `python scraper.py` as usual. The scraper will automatically load your custom ignored strings.