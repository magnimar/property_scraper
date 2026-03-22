from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from urllib.parse import urljoin

import base64
import os
import json
import requests
import re

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )


DEFAULT_CONFIG_PATH = "config.json"


def load_config_user_list(config_path: Optional[str] = None) -> list:
    """Load config.json: must be a JSON array of user objects (see config.example.json)."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not os.path.exists(path):
        logging.error("Configuration file '%s' not found.", path)
        raise SystemExit(1)
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            logging.error("Could not decode JSON from '%s'.", path)
            raise SystemExit(1) from None

    if not isinstance(data, list):
        logging.error(
            "config.json must be a JSON array (list) of user objects, not %s.",
            type(data).__name__,
        )
        raise SystemExit(1)

    if len(data) == 0:
        logging.error("config.json user list is empty; add at least one user object.")
        raise SystemExit(1)

    seen_users: dict[str, int] = {}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            logging.error(
                "config.json[%d] must be a JSON object, not %s.",
                i,
                type(item).__name__,
            )
            raise SystemExit(1)
        uid = item.get("user")
        if not uid or not isinstance(uid, str):
            logging.error(
                'config.json[%d] must include a non-empty string "user" id.',
                i,
            )
            raise SystemExit(1)
        if uid in seen_users:
            logging.error(
                'Duplicate "user" %r in config.json at indices %d and %d.',
                uid,
                seen_users[uid],
                i,
            )
            raise SystemExit(1)
        seen_users[uid] = i

    return data


def find_user_config(user_id: str, config_path: Optional[str] = None) -> dict:
    """Return the config object for --user (must match the \"user\" field)."""
    for item in load_config_user_list(config_path):
        if item.get("user") == user_id:
            return item
    logging.error(
        "User %r not found in '%s' (no matching \"user\" field).",
        user_id,
        config_path or DEFAULT_CONFIG_PATH,
    )
    raise SystemExit(1)


def run_schedule_loop():
    """Wait until SCRAPER_HOUR:SCRAPER_MINUTE daily, then run Scraper for each config user in parallel."""
    from dotenv import load_dotenv

    load_dotenv()

    try:
        hour = int(os.environ["SCRAPER_HOUR"])
        minute = int(os.environ["SCRAPER_MINUTE"])
    except KeyError:
        logging.error(
            "Schedule mode requires SCRAPER_HOUR and SCRAPER_MINUTE in the environment "
            "(e.g. in a .env file)."
        )
        raise SystemExit(1) from None
    except ValueError:
        logging.error("SCRAPER_HOUR and SCRAPER_MINUTE must be integers.")
        raise SystemExit(1) from None

    logging.info(
        "Schedule mode: will run daily at %02d:%02d for each user in config.json (in parallel).",
        hour,
        minute,
    )

    last_run_date = None
    while True:
        now = datetime.now()
        if now.hour == hour and now.minute == minute:
            if last_run_date == now.date():
                time.sleep(1)
                continue
            last_run_date = now.date()

            try:
                user_configs = load_config_user_list()
            except SystemExit:
                logging.error(
                    "Invalid config.json; skipping today's scheduled batch.",
                )
                time.sleep(60)
                continue

            logging.info(
                "Running scheduled batch for: %s",
                ", ".join(c["user"] for c in user_configs),
            )

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [
                    executor.submit(_run_scraper_for_user, uc) for uc in user_configs
                ]
                for future in futures:
                    future.result()  # Wait for all scrapers to complete

        time.sleep(1)


def _run_scraper_for_user(user_config: dict):
    """Helper function to run the scraper for a single user and handle exceptions."""
    uid = user_config["user"]
    logging.info("Running scraper for %s...", uid)
    try:
        Scraper(user_config).main()
    except SystemExit as e:
        if e.code not in (0, None):
            logging.error(
                "Scraper exited with code %s for user %s",
                e.code,
                uid,
            )
    except Exception:
        logging.exception("Scraper failed for user %s", uid)


class Scraper:
    def __init__(self, user_config: dict):
        """user_config: one element from the config.json array (must include \"user\" and settings)."""
        self.user_config = user_config
        self.args = argparse.Namespace(user=user_config["user"])

        self.API_KEY = self.user_config.get("BREVO_API_KEY")
        self.FROM_EMAIL = self.user_config.get("FROM_EMAIL")
        self.TO_EMAIL = self.user_config.get("TO_EMAIL")
        self.MIN_PRICE = self.user_config.get("MIN_PRICE")
        self.MAX_PRICE = self.user_config.get("MAX_PRICE")
        self.MIN_BEDROOMS = self.user_config.get("MIN_BEDROOMS")
        self.MAX_BEDROOMS = self.user_config.get("MAX_BEDROOMS")
        self.ZIP_CODES = self.user_config.get("ZIP_CODES")

        # Property categories
        categories = []
        if str(self.user_config.get("EINBYLISHUS", "")).lower() == "yes":
            categories.append("1")
        if str(self.user_config.get("FJOLBYLISHUS", "")).lower() == "yes":
            categories.append("2")
        if str(self.user_config.get("ATVINNUHUSNAEDI", "")).lower() == "yes":
            categories.append("3")
        if str(self.user_config.get("RADHUS_PARHUS", "")).lower() == "yes":
            categories.append("4")
        if str(self.user_config.get("SUMARHUS", "")).lower() == "yes":
            categories.append("6")
        if str(self.user_config.get("PARHUS", "")).lower() == "yes":
            categories.append("7")
        if str(self.user_config.get("JORD_LOD", "")).lower() == "yes":
            categories.append("8")
        if str(self.user_config.get("HAED", "")).lower() == "yes":
            categories.append("17")
        if str(self.user_config.get("HESTHUS", "")).lower() == "yes":
            categories.append("35")
        if str(self.user_config.get("OFLOKKAD", "")).lower() == "yes":
            categories.append("36")

        # If no categories specified, fallback to original default
        self.CATEGORIES = ",".join(categories) if categories else "2,1,4,7,17"

    def fetch_image_as_data_uri(self, image_url, referer=None, max_size_kb=500):
        """Fetch image from URL and return a data URI for embedding, or None on failure."""
        if not image_url or not image_url.startswith("http"):
            return None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        if referer:
            headers["Referer"] = referer
        try:
            r = requests.get(image_url, timeout=15, headers=headers)
            r.raise_for_status()
            content = r.content
            if len(content) > max_size_kb * 1024:
                return None
            content_type = (
                r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            )
            if content_type not in (
                "image/jpeg",
                "image/png",
                "image/gif",
                "image/webp",
            ):
                content_type = "image/jpeg"
            b64 = base64.b64encode(content).decode("ascii")
            return f"data:{content_type};base64,{b64}"
        except Exception:
            return None

    def send_email_notification(self, subject, html_body):
        if not all([self.API_KEY, self.FROM_EMAIL, self.TO_EMAIL]):
            logging.warning(
                "Email sending skipped due to missing API_KEY, FROM_EMAIL, or TO_EMAIL in config."
            )
            return False

        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key["api-key"] = self.API_KEY
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )

        sender = {"name": "Property Scraper", "email": self.FROM_EMAIL}
        to = [{"email": self.TO_EMAIL}]

        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=to, html_content=html_body, sender=sender, subject=subject
        )

        logging.info(f"Attempting to send email to {self.TO_EMAIL}...")
        try:
            api_response = api_instance.send_transac_email(send_smtp_email)
            logging.info(
                f"Email sent successfully! Message ID: {api_response.message_id}"
            )
            return True
        except ApiException as e:
            logging.error(
                f"Exception when calling TransactionalEmailsApi->send_transac_email: {e}"
            )
            return False

    NO_SEARCH_RESULTS_TEXT = "Leitin skilaði engum niðurstöðum."
    LISTING_AJAX_URL = "https://fasteignir.visir.is/ajaxsearch/getresults"

    def _search_listings_query_params(self, page: int) -> dict:
        """Query string for /ajaxsearch/getresults (same keys as the in-browser hash route)."""
        # 1 is einbýlishús
        # 2 is fjölbýlishús
        # 3 is atvinnuhúsnæði
        # 4 is raðhús / parhús
        # 6 is sumarhús
        # 7 is parhús
        # 8 is jörð / lóð
        # 17 is hæð
        # 35 is hesthús
        # 36 is óflokkað
        return {
            "stype": "sale",
            "zip": self.ZIP_CODES,
            "price": f"{self.MIN_PRICE},{self.MAX_PRICE}",
            "bedroom": f"{self.MIN_BEDROOMS},{self.MAX_BEDROOMS}",
            "category": self.CATEGORIES,
            "page": page,
        }

    def _parse_listing_cards_from_html(
        self, html: str, base_url: str, skip_address_substrings, processed_links: set
    ) -> tuple[list, int]:
        """Parse estate cards from HTML. Returns (new prop dicts, raw card count on page)."""
        soup = BeautifulSoup(html, "html.parser")
        property_cards = soup.find_all(
            "div", class_=lambda c: c and "estate__item" in c
        )
        raw_count = len(property_cards)
        out = []
        for card in property_cards:
            link_tag = card.find("a", class_="js-property-link", href=True)
            address_tag = card.find("div", class_="estate__item-title")
            price_tag = card.find("div", class_="estate__price")
            size_tag = card.find("div", class_="estate__parameters--1")
            rooms_tag = card.find("div", class_="estate__parameters--2")
            bedrooms_tag = card.find("div", class_="estate__parameters--4")

            image_tag = card.find("img")
            image_url = None
            if image_tag and image_tag.get("src"):
                image_url = urljoin(base_url, image_tag["src"])
            elif image_tag and image_tag.get("data-src"):
                image_url = urljoin(base_url, image_tag["data-src"])

            link = urljoin(base_url, link_tag["href"]) if link_tag else "N/A"
            address = (
                address_tag.get_text(strip=True, separator=" ")
                if address_tag
                else "N/A"
            )

            if re.search(r"\bseld\b", address, re.IGNORECASE):
                continue

            if any(
                substring.lower() in address.lower()
                for substring in skip_address_substrings
            ):
                continue

            price_str = price_tag.get_text(strip=True) if price_tag else "N/A"
            if price_str == "Tilboð":
                continue

            try:
                price_num = int(price_str.replace(".", "").replace(" kr", ""))
                if not int(self.MIN_PRICE) <= price_num <= int(self.MAX_PRICE):
                    continue
            except (ValueError, TypeError):
                continue

            size = size_tag.get_text(strip=True) if size_tag else "N/A"
            total_rooms = rooms_tag.get_text(strip=True) if rooms_tag else "N/A"
            bedrooms_text = bedrooms_tag.get_text(strip=True) if bedrooms_tag else "N/A"
            bedrooms = "1" if bedrooms_text == "N/A" else bedrooms_text

            price_per_m2 = None
            if size != "N/A" and price_num:
                try:
                    size_num = float(size.replace("m²", "").replace(",", "."))
                    if size_num > 0:
                        price_per_m2 = int(price_num / size_num)
                except (ValueError, TypeError):
                    pass

            if link != "N/A" and address != "N/A":
                if link in processed_links:
                    continue
                processed_links.add(link)
                out.append(
                    {
                        "address": address,
                        "price": price_str,
                        "size_m2": size,
                        "price_per_m2": price_per_m2,
                        "total_rooms": total_rooms,
                        "bedrooms": bedrooms,
                        "link": link,
                        "image_url": image_url,
                    }
                )
        return out, raw_count

    def scrape_visir_properties(self):
        base_url = "https://fasteignir.visir.is"

        if not all(
            [
                self.MIN_PRICE,
                self.MAX_PRICE,
                self.MIN_BEDROOMS,
                self.MAX_BEDROOMS,
                self.ZIP_CODES,
            ]
        ):
            logging.error("Missing search parameters in config file.")
            return [], None

        skip_address_substrings = self.user_config.get("ignored_strings", [])

        new_properties_found_this_run = []
        processed_links = set()

        headers = self._page_request_headers()
        headers["Referer"] = "https://fasteignir.visir.is/search/results/?stype=sale"

        page_num = 1
        max_pages = 500

        logging.info(
            "Fetching search pages via requests → %s (page=1, 2, … until no hits).",
            self.LISTING_AJAX_URL,
        )

        while page_num <= max_pages:
            try:
                response = requests.get(
                    self.LISTING_AJAX_URL,
                    params=self._search_listings_query_params(page_num),
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
                text = response.text
            except Exception as e:
                logging.error("Error fetching search page %s: %s", page_num, e)
                break

            if self.NO_SEARCH_RESULTS_TEXT in text:
                logging.info(
                    "Page %s: '%s' — stopping pagination.",
                    page_num,
                    self.NO_SEARCH_RESULTS_TEXT,
                )
                break

            added, raw_cards = self._parse_listing_cards_from_html(
                text, base_url, skip_address_substrings, processed_links
            )
            logging.info(
                "Page %s: %s card(s) on page, %s new after filters (running total %s).",
                page_num,
                raw_cards,
                len(added),
                len(processed_links),
            )

            if raw_cards == 0:
                logging.warning(
                    "Page %s: no listing cards in HTML and no empty-search message — stopping.",
                    page_num,
                )
                break

            new_properties_found_this_run.extend(added)
            page_num += 1
            time.sleep(0.5)

        return new_properties_found_this_run, None

    def get_numeric_price(self, price_str):
        try:
            return int(price_str.replace(".", "").replace(" kr", ""))
        except (ValueError, TypeError):
            return 0

    def _page_request_headers(self):
        """Same browser-like headers as image fetch (Referer set per-request)."""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def check_property_details(self, prop):
        """Fetch property detail page with requests (balcony, terrace, image)."""
        if not prop.get("link"):
            return prop

        try:
            headers = self._page_request_headers()
            headers["Referer"] = "https://fasteignir.visir.is/"
            response = requests.get(prop["link"], timeout=15, headers=headers)
            response.raise_for_status()

            page_text = response.text.lower()
            soup = BeautifulSoup(response.text, "html.parser")

            if prop.get("has_balcony") is None:
                prop["has_balcony"] = "svalir" in page_text
            if prop.get("has_terrace") is None:
                prop["has_terrace"] = "sérafnota" in page_text or "garð" in page_text
            if prop.get("has_garage") is None:
                prop["has_garage"] = "bílskúr" in page_text

            if prop.get("build_year") is None:
                match = re.search(
                    r"bygg(?:t|ingará[\w]*?)[^\d]{0,20}(\d{4})", page_text
                )
                if match:
                    prop["build_year"] = match.group(1)
                else:
                    prop["build_year"] = "N/A"

            if prop.get("fasteignamat") is None:
                fmat_elem = soup.find(string=re.compile("Fasteignamat", re.I))
                if (
                    fmat_elem
                    and fmat_elem.parent
                    and fmat_elem.parent.find_next_sibling()
                ):
                    prop["fasteignamat"] = (
                        fmat_elem.parent.find_next_sibling().get_text(strip=True)
                    )
                else:
                    prop["fasteignamat"] = "N/A"

            if not prop.get("image_url") or "staticmap" in (
                prop.get("image_url") or ""
            ):
                img_tag = soup.find(
                    "img",
                    src=lambda s: s and "api-beta.fasteignir.is/pictures" in s,
                )
                if not img_tag:
                    for img in soup.find_all("img", attrs={"data-src": True}):
                        if img.get(
                            "data-src"
                        ) and "api-beta.fasteignir.is/pictures" in img.get(
                            "data-src", ""
                        ):
                            img_tag = img
                            break
                if img_tag:
                    image_url = img_tag.get("src") or img_tag.get("data-src")
                    if image_url:
                        if not image_url.startswith("http"):
                            image_url = urljoin(prop["link"], image_url)
                        prop["image_url"] = image_url

        except Exception as e:
            logging.warning(
                "Failed to check details for %s: %s", prop.get("address"), e
            )
            if prop.get("has_balcony") is None:
                prop["has_balcony"] = False
            if prop.get("has_terrace") is None:
                prop["has_terrace"] = False
            if prop.get("has_garage") is None:
                prop["has_garage"] = False
            if prop.get("build_year") is None:
                prop["build_year"] = "N/A"
            if prop.get("fasteignamat") is None:
                prop["fasteignamat"] = "N/A"

        return prop

    @staticmethod
    def _get_location_names(zip_code: str) -> tuple[str, str]:
        """Returns (nominative_name, dative_name) for a given zip code."""
        reykjavik_zips = {
            "101",
            "102",
            "103",
            "104",
            "105",
            "107",
            "108",
            "109",
            "110",
            "111",
            "112",
            "113",
            "116",
            "161",
            "162",
        }
        kopavogur_zips = {"200", "201", "202", "203", "206"}
        gardabaer_zips = {"210", "212", "225"}
        hafnarfjordur_zips = {"220", "221", "222"}
        mosfellsbaer_zips = {"270", "271", "276"}
        seltjarnarnes_zips = {"170"}
        keflavik_zips = {"230", "232"}
        hafnir_zips = {"233"}
        reykjanesbaer_zips = {"262"}
        grindavik_zips = {"240", "241"}
        sudurnesjabaer_zips = {"245", "246", "250", "251"}
        njardvik_zips = {"260"}
        vogar_zips = {"190", "191"}
        selfoss_zips = {"800", "801", "802", "803", "804", "805", "806"}
        hveragerdi_zips = {"810"}
        thorlakshofn_zips = {"815"}
        olfus_zips = {"816"}
        eyrarbakki_zips = {"820"}
        stokkseyri_zips = {"825"}
        laugarvatn_zips = {"840"}
        fludir_zips = {"845", "846"}
        hella_zips = {"850", "851"}
        hvolsvollur_zips = {"860", "861"}
        vik_zips = {"870", "871"}
        kirkjubaejarklaustur_zips = {"880", "881"}
        vestmannaeyjar_zips = {"900"}
        vestmannaeyjabaer_zips = {"901"}

        if zip_code in reykjavik_zips:
            return "Reykjavík", "Reykjavík"
        if zip_code in kopavogur_zips:
            return "Kópavogur", "Kópavogi"
        if zip_code in gardabaer_zips:
            return "Garðabær", "Garðabæ"
        if zip_code in hafnarfjordur_zips:
            return "Hafnarfjörður", "Hafnarfirði"
        if zip_code in mosfellsbaer_zips:
            return "Mosfellsbær", "Mosfellsbæ"
        if zip_code in seltjarnarnes_zips:
            return "Seltjarnarnes", "Seltjarnarnesi"
        if zip_code in keflavik_zips:
            return "Keflavík", "Keflavík"
        if zip_code in hafnir_zips:
            return "Hafnir", "Höfnum"
        if zip_code in reykjanesbaer_zips:
            return "Reykjanesbær", "Reykjanesbæ"
        if zip_code in grindavik_zips:
            return "Grindavík", "Grindavík"
        if zip_code in sudurnesjabaer_zips:
            return "Suðurnesjabær", "Suðurnesjabæ"
        if zip_code in njardvik_zips:
            return "Njarðvík", "Njarðvík"
        if zip_code in vogar_zips:
            return "Vogar", "Vogum"
        if zip_code in selfoss_zips:
            return "Selfoss", "Selfossi"
        if zip_code in hveragerdi_zips:
            return "Hveragerði", "Hveragerði"
        if zip_code in thorlakshofn_zips:
            return "Þorlákshöfn", "Þorlákshöfn"
        if zip_code in olfus_zips:
            return "Ölfus", "Ölfusi"
        if zip_code in eyrarbakki_zips:
            return "Eyrarbakki", "Eyrarbakka"
        if zip_code in stokkseyri_zips:
            return "Stokkseyri", "Stokkseyri"
        if zip_code in laugarvatn_zips:
            return "Laugarvatn", "Laugarvatni"
        if zip_code in fludir_zips:
            return "Flúðir", "Flúðum"
        if zip_code in hella_zips:
            return "Hella", "Hellu"
        if zip_code in hvolsvollur_zips:
            return "Hvolsvöllur", "Hvolsvelli"
        if zip_code in vik_zips:
            return "Vík", "Vík"
        if zip_code in kirkjubaejarklaustur_zips:
            return "Kirkjubæjarklaustur", "Kirkjubæjarklaustri"
        if zip_code in vestmannaeyjar_zips:
            return "Vestmannaeyjar", "Vestmannaeyjum"
        if zip_code in vestmannaeyjabaer_zips:
            return "Vestmannaeyjabær", "Vestmannaeyjabæ"

        return "", ""

    def generate_property_html(self, properties, title):
        html = f"<h2>{title}</h2>"
        for prop in properties:
            html += "<div style='margin-bottom: 30px; padding: 15px; border: 1px solid #ddd;'>"
            html += f"<h3>{prop['address']}</h3>"
            html += f"<p><strong>Verð:</strong> {prop['price']}</p>"
            if prop.get("fasteignamat") and prop["fasteignamat"] != "N/A":
                html += f"<p><strong>Fasteignamat:</strong> {prop['fasteignamat']}</p>"
            if prop.get("price_per_m2"):
                price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                html += f"<p><strong>Fermetraverð:</strong> {price_per_m2_formatted} kr.</p>"
            html += f"<p><strong>Stærð:</strong> {prop['size_m2']}</p>"
            html += f"<p><strong>Svefnherbergi:</strong> {prop['bedrooms']}</p>"

            # Calculate monthly payment for an 80% non-indexed loan over 40 years
            try:
                numeric_price = int(prop["price"].replace(".", "").replace(" kr", ""))
                loan_70 = numeric_price * 0.70
                loan_10 = numeric_price * 0.10
                loan_80 = numeric_price * 0.80

                interest_payment = int(
                    (loan_70 * 0.007116666666666666) + (loan_10 * 0.007708333333333334)
                )
                principal_payment = int(loan_80 * 0.00023890801001251563)

                monthly_payment = interest_payment + principal_payment

                monthly_formatted = f"{monthly_payment:,}".replace(",", ".")

                html += f"<p><strong>Mánaðarleg afborgun (Óverðtryggt, 40 ár, 80% lán):</strong> {monthly_formatted} kr.</p>"
            except (ValueError, TypeError, KeyError):
                pass

            if prop.get("build_year") and prop["build_year"] != "N/A":
                html += f"<p><strong>Byggt:</strong> {prop['build_year']}</p>"
            if prop.get("has_balcony") is not None:
                html += f"<p><strong>Svalir:</strong> {'Já' if prop['has_balcony'] else 'Nei'}</p>"
            if prop.get("has_terrace") is not None:
                html += f"<p><strong>Garður:</strong> {'Já' if prop['has_terrace'] else 'Nei'}</p>"
            if prop.get("has_garage") is not None:
                html += f"<p><strong>Bílskúr:</strong> {'Já' if prop['has_garage'] else 'Nei'}</p>"
            if prop.get("image_url"):
                html += f"<img src='{prop['image_url']}' alt='Property image' style='max-width: 600px; height: auto; margin: 10px 0;' />"
            html += f"<p><a href='{prop['link']}'>View Property</a></p>"
            html += "</div>"
        return html

    def print_properties(self, properties, title):
        logging.info(f"\n--- {title} ---")
        for i, prop in enumerate(properties):
            logging.info(f"\nProperty #{i+1}")
            logging.info(f"  Address: {prop['address']}")
            logging.info(f"  Price: {prop['price']}")
            if prop.get("fasteignamat") and prop["fasteignamat"] != "N/A":
                logging.info(f"  Fasteignamat: {prop['fasteignamat']}")
            logging.info(f"  Size: {prop['size_m2']}")
            if prop.get("price_per_m2"):
                price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                logging.info(f"  Price per m²: {price_per_m2_formatted} kr.")
            logging.info(f"  Bedrooms: {prop['bedrooms']}")

            try:
                numeric_price = int(prop["price"].replace(".", "").replace(" kr", ""))
                loan_70 = numeric_price * 0.70
                loan_10 = numeric_price * 0.10
                loan_80 = numeric_price * 0.80

                interest_payment = int(
                    (loan_70 * 0.007116666666666666) + (loan_10 * 0.007708333333333334)
                )
                principal_payment = int(loan_80 * 0.00023890801001251563)

                monthly_payment = interest_payment + principal_payment

                monthly_formatted = f"{monthly_payment:,}".replace(",", ".")
                principal_formatted = f"{principal_payment:,}".replace(",", ".")

                logging.info(
                    f"  Monthly Payment (Non-indexed, 40 yrs, 80% loan): {monthly_formatted} kr."
                )
                logging.info(f"  Principal Paid Down: {principal_formatted} kr.")
            except (ValueError, TypeError, KeyError):
                pass

            if prop.get("build_year") and prop["build_year"] != "N/A":
                logging.info(f"  Built: {prop['build_year']}")
            if prop.get("has_balcony") is not None:
                logging.info(f"  Balcony: {'yes' if prop['has_balcony'] else 'no'}")
            if prop.get("has_terrace") is not None:
                logging.info(f"  Terrace: {'yes' if prop['has_terrace'] else 'no'}")
            if prop.get("has_garage") is not None:
                logging.info(f"  Garage: {'yes' if prop['has_garage'] else 'no'}")
            logging.info(f"  Link: {prop['link']}")

    def main(self):
        logging.info(f"Start time: {time.time()}")
        new_properties, _driver = self.scrape_visir_properties()
        logging.info(f"After having properties, time: {time.time()}")

        def needs_detail_check(prop):
            return (
                prop.get("has_balcony") is None
                or prop.get("has_terrace") is None
                or prop.get("has_garage") is None
                or prop.get("build_year") is None
                or prop.get("fasteignamat") is None
                or not prop.get("image_url")
                or "staticmap" in (prop.get("image_url") or "")
            )

        to_check = [p for p in new_properties if needs_detail_check(p)]
        logging.info(
            "Checking %d / %d properties in parallel (requests)...",
            len(to_check),
            len(new_properties),
        )
        if to_check:
            with ThreadPoolExecutor(max_workers=15) as executor:
                list(executor.map(self.check_property_details, to_check))

        new_properties.sort(key=lambda x: self.get_numeric_price(x["price"]))
        logging.info(f"After sorting properties, time: {time.time()}")

        # only keep properties with a balcony, terrace or garage
        new_properties = [
            prop
            for prop in new_properties
            if prop.get("has_balcony")
            or prop.get("has_terrace")
            or prop.get("has_garage")
        ]
        logging.info(
            f"Found {len(new_properties)} properties with a balcony, terrace or garage."
        )

        # --- Split properties by zip code ---
        import re

        allowed_zips = [
            z.strip() for z in (self.ZIP_CODES or "").split(",") if z.strip()
        ]

        properties_by_zip = {}
        for prop in new_properties:
            zip_code = "Annað"
            matches = list(re.finditer(r"(?<!\d)\d{3}(?!\d)", prop["address"]))
            for match in reversed(matches):
                val = match.group()
                if val in allowed_zips:
                    zip_code = val
                    start = match.start()
                    prefix = prop["address"][:start].rstrip()
                    if not prefix.endswith(","):
                        prop["address"] = prefix + ", " + prop["address"][start:]
                    break

            properties_by_zip.setdefault(zip_code, []).append(prop)

        for zip_code, props in properties_by_zip.items():
            props.sort(
                key=lambda p: (
                    p.get("price_per_m2") is None,
                    p.get("price_per_m2") if p.get("price_per_m2") is not None else 0,
                )
            )
            base_name, dative_name = self._get_location_names(zip_code)
            if base_name:
                title = f"Fasteignir í {zip_code} {dative_name}"
            elif zip_code != "Annað":
                title = f"Fasteignir í {zip_code}"
            else:
                title = "Fasteignir (óþekkt póstnúmer)"
            self.print_properties(props, title)

        if new_properties:
            subject = f"Fann {len(new_properties)} eignir fyrir þig"

            avg_price_per_m2 = {}
            bedroom_counts = {}
            for prop in new_properties:
                bedrooms = prop.get("bedrooms", "N/A")
                if bedrooms not in avg_price_per_m2:
                    avg_price_per_m2[bedrooms] = 0
                    bedroom_counts[bedrooms] = 0

                if prop.get("price_per_m2"):
                    avg_price_per_m2[bedrooms] += prop["price_per_m2"]
                    bedroom_counts[bedrooms] += 1

            for bedrooms, total_price in avg_price_per_m2.items():
                if bedroom_counts[bedrooms] > 0:
                    avg_price_per_m2[bedrooms] = int(
                        total_price / bedroom_counts[bedrooms]
                    )

            logging.info("Embedding property images for email...")
            for prop in new_properties:
                if prop.get("image_url"):
                    self.fetch_image_as_data_uri(
                        prop["image_url"], referer=prop.get("link")
                    )

            html_body = "<html><body>"

            html_body += "<h2>Meðalfermetraverð eftir hverfi:</h2>"
            html_body += "<ul>"

            for zip_code in allowed_zips + ["Annað"]:
                if zip_code in properties_by_zip:
                    zip_props = properties_by_zip[zip_code]
                    zip_total_m2_price = sum(
                        p.get("price_per_m2", 0)
                        for p in zip_props
                        if p.get("price_per_m2")
                    )
                    zip_props_with_m2 = sum(
                        1 for p in zip_props if p.get("price_per_m2")
                    )

                    if zip_props_with_m2 > 0:
                        zip_avg_m2 = int(zip_total_m2_price / zip_props_with_m2)
                        zip_avg_m2_formatted = f"{zip_avg_m2:,}".replace(",", ".")

                        base_name, _ = self._get_location_names(zip_code)
                        if base_name:
                            zip_label = f"{zip_code} {base_name}"
                        elif zip_code != "Annað":
                            zip_label = zip_code
                        else:
                            zip_label = "Óþekkt"

                        html_body += f"<li><strong>{zip_label}:</strong> {zip_avg_m2_formatted} kr.</li>"
            html_body += "</ul>"

            html_body += "<h2>Meðalfermetraverð eftir herbergjafjölda:</h2>"
            html_body += "<ul>"
            for bedrooms, avg_price in sorted(avg_price_per_m2.items()):
                avg_price_formatted = f"{avg_price:,}".replace(",", ".")
                html_body += f"<li><strong>{bedrooms} svefnherbergi:</strong> {avg_price_formatted} kr.</li>"
            html_body += "</ul>"

            html_body += "<h2>Meðalfermetraverð eftir herbergjafjölda og hverfi:</h2>"
            for bedrooms in sorted(avg_price_per_m2.keys()):
                html_body += f"<h3>{bedrooms} svefnherbergi:</h3>"
                html_body += "<ul>"
                for zip_code in allowed_zips + ["Annað"]:
                    if zip_code in properties_by_zip:
                        zip_props = properties_by_zip[zip_code]
                        zip_props_bed = [
                            p for p in zip_props if p.get("bedrooms", "N/A") == bedrooms
                        ]

                        zip_total_m2_price_bed = sum(
                            p.get("price_per_m2", 0)
                            for p in zip_props_bed
                            if p.get("price_per_m2")
                        )
                        zip_props_with_m2_bed = sum(
                            1 for p in zip_props_bed if p.get("price_per_m2")
                        )

                        if zip_props_with_m2_bed > 0:
                            zip_avg_m2_bed = int(
                                zip_total_m2_price_bed / zip_props_with_m2_bed
                            )
                            zip_avg_m2_formatted_bed = f"{zip_avg_m2_bed:,}".replace(
                                ",", "."
                            )

                            base_name, _ = self._get_location_names(zip_code)
                            if base_name:
                                zip_label = f"{zip_code} {base_name}"
                            elif zip_code != "Annað":
                                zip_label = zip_code
                            else:
                                zip_label = "Óþekkt"

                            html_body += f"<li><strong>{zip_label}:</strong> {zip_avg_m2_formatted_bed} kr.</li>"
                html_body += "</ul>"

            html_body += "<hr>"

            for zip_code in allowed_zips + ["Annað"]:
                if zip_code in properties_by_zip:
                    base_name, dative_name = self._get_location_names(zip_code)
                    if base_name:
                        title = f"Fasteignir í {zip_code} {dative_name}"
                    elif zip_code != "Annað":
                        title = f"Fasteignir í {zip_code}"
                    else:
                        title = "Fasteignir (óþekkt póstnúmer)"

                    # Calculate average price per m2 for this specific zip code
                    zip_props = properties_by_zip[zip_code]

                    html_body += self.generate_property_html(zip_props, title)
                    html_body += "<hr>"

            html_body += "</body></html>"

            logging.info("\nAttempting to send email notification...")
            self.send_email_notification(subject, html_body)
        else:
            logging.info("\nNo properties found. No email notification sent.")


def _parse_args():
    parser = argparse.ArgumentParser(description="Scrape real estate listings.")
    parser.add_argument(
        "--user",
        help='Match the "user" field of one object in the config.json array.',
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help=(
            "Run in a loop: each day at SCRAPER_HOUR:SCRAPER_MINUTE (from .env), "
            "run once per user in config.json list order."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    _configure_logging()
    args = _parse_args()
    if args.schedule:
        if args.user:
            logging.error("Do not pass --user with --schedule.")
            raise SystemExit(2)
        run_schedule_loop()
    else:
        if not args.user:
            logging.error("Either --user NAME or --schedule is required.")
            raise SystemExit(2)
        Scraper(find_user_config(args.user)).main()
