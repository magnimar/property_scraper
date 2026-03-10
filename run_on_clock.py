#!/usr/bin/env python3
"""Run scraper for magni then gabriela when the clock hits 22:00."""

import logging
import os
import subprocess
import time
from datetime import datetime

from dotenv import load_dotenv


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    load_dotenv()

    hour = int(os.environ.get("SCRAPER_HOUR"))

    while True:
        current_time = datetime.now()
        if current_time.hour == hour and current_time.minute == 0:
            logging.info("Running scraper for magni...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "magni"])

            time.sleep(60)

            logging.info("Running scraper for gabriela...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "gabriela"])

            time.sleep(60)
            
            logging.info("Running scraper for halli...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "halli"])

            time.sleep(60)
            
            logging.info("Running scraper for leon...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "leon"])

            time.sleep(60)

        time.sleep(1)


if __name__ == "__main__":
    main()
