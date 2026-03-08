#!/usr/bin/env python3
"""Run scraper for magni then gabriela when the clock hits 22:00."""

import subprocess
import time
from datetime import datetime


def main():

    while True:
        current_time = datetime.now()
        if current_time.hour == 22 and current_time.minute == 0:
            print("Running scraper for magni...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "magni"])

            time.sleep(60)

            print("Running scraper for gabriela...")
            subprocess.run(["venv/bin/python", "scraper.py", "--user", "gabriela"])

            time.sleep(60)

        time.sleep(1)


if __name__ == "__main__":
    main()
