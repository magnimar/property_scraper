import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup
from urllib.parse import urljoin

import base64
import os
import json
import requests

import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException


class Scraper:
    def __init__(self):
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
        )
        self.config_file = "config.json"

        # --- User-specific Setup ---
        parser = argparse.ArgumentParser(description="Scrape real estate listings.")
        parser.add_argument(
            "--user",
            required=True,
            help="The user running the script (e.g., 'magni', 'gabriela').",
        )
        self.args = parser.parse_args()

        config = self.load_config()

        if self.args.user not in config:
            logging.error(f"User '{self.args.user}' not found in '{self.config_file}'.")
            exit()

        self.user_config = config[self.args.user]

        self.API_KEY = self.user_config.get("BREVO_API_KEY")
        self.FROM_EMAIL = self.user_config.get("FROM_EMAIL")
        self.TO_EMAIL = self.user_config.get("TO_EMAIL")
        self.MIN_PRICE = self.user_config.get("MIN_PRICE")
        self.MAX_PRICE = self.user_config.get("MAX_PRICE")
        self.MIN_BEDROOMS = self.user_config.get("MIN_BEDROOMS")
        self.MAX_BEDROOMS = self.user_config.get("MAX_BEDROOMS")
        self.ZIP_CODES = self.user_config.get("ZIP_CODES")

    def load_config(self):
        """Loads the entire configuration from config.json."""
        if not os.path.exists(self.config_file):
            logging.error(f"Configuration file '{self.config_file}' not found.")
            exit()
        with open(self.config_file, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logging.error(f"Could not decode JSON from '{self.config_file}'.")
                exit()

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

    # Visir shows this when there are no hits (empty search).
    NO_SEARCH_RESULTS_TEXT = "Leitin skilaði engum niðurstöðum."
    # SPA loads listings via GET with the same params as the hash (#/?zip=…&page=N).
    LISTING_AJAX_URL = "https://fasteignir.visir.is/ajaxsearch/getresults"

    def _search_listings_query_params(self, page: int) -> dict:
        """Query string for /ajaxsearch/getresults (same keys as the in-browser hash route)."""
        return {
            "stype": "sale",
            "zip": self.ZIP_CODES,
            "price": f"{self.MIN_PRICE},{self.MAX_PRICE}",
            "bedroom": f"{self.MIN_BEDROOMS},{self.MAX_BEDROOMS}",
            "category": "2,1,4,7,17",
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
            bedrooms_text = (
                bedrooms_tag.get_text(strip=True) if bedrooms_tag else "N/A"
            )
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

        return prop

    def generate_property_html(self, properties, title):
        html = f"<h2>{title}</h2>"
        for prop in properties:
            html += "<div style='margin-bottom: 30px; padding: 15px; border: 1px solid #ddd;'>"
            html += f"<h3>{prop['address']}</h3>"
            html += f"<p><strong>Verð:</strong> {prop['price']}</p>"
            if prop.get("price_per_bedroom") is not None:
                ppb_formatted = f"{int(prop['price_per_bedroom']):,}".replace(
                    ",",
                    ".",
                )
                html += f"<p><strong>Verð per svefnherbergi:</strong> {ppb_formatted} kr.</p>"
            if prop.get("price_per_m2"):
                price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                html += f"<p><strong>Fermetraverð:</strong> {price_per_m2_formatted} kr.</p>"
            html += f"<p><strong>Stærð:</strong> {prop['size_m2']}</p>"
            html += f"<p><strong>Svefnherbergi:</strong> {prop['bedrooms']}</p>"
            if prop.get("has_balcony") is not None:
                html += f"<p><strong>Svalir:</strong> {'Já' if prop['has_balcony'] else 'Nei'}</p>"
            if prop.get("has_terrace") is not None:
                html += f"<p><strong>Garður:</strong> {'Já' if prop['has_terrace'] else 'Nei'}</p>"
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
            logging.info(f"  Size: {prop['size_m2']}")
            if prop.get("price_per_m2"):
                price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                logging.info(f"  Price per m²: {price_per_m2_formatted} kr.")
            logging.info(f"  Bedrooms: {prop['bedrooms']}")
            if prop.get("has_balcony") is not None:
                logging.info(f"  Balcony: {'yes' if prop['has_balcony'] else 'no'}")
            if prop.get("has_terrace") is not None:
                logging.info(f"  Terrace: {'yes' if prop['has_terrace'] else 'no'}")
            logging.info(f"  Link: {prop['link']}")
            logging.info(f"  Price per bedroom: {prop['price_per_bedroom']}")

    def main(self):
        logging.info(f"Start time: {time.time()}")
        new_properties, _driver = self.scrape_visir_properties()
        logging.info(f"After having properties, time: {time.time()}")

        def needs_detail_check(prop):
            return (
                prop.get("has_balcony") is None
                or prop.get("has_terrace") is None
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

        # only keep properties with a balcony or terrace
        new_properties = [
            prop
            for prop in new_properties
            if prop.get("has_balcony") or prop.get("has_terrace")
        ]
        logging.info(
            f"Found {len(new_properties)} properties with a balcony or terrace."
        )

        # --- Calculate average price ---
        total_price = 0
        property_count = 0
        for prop in new_properties:
            try:
                price = int(prop["price"].replace(".", "").replace(" kr", ""))
                total_price += price
                property_count += 1
            except (ValueError, TypeError):
                continue

        average_price = total_price / property_count if property_count > 0 else 0

        # --- Split properties into under and over average ---
        under_average = []
        over_average = []
        for prop in new_properties:

            # calculate price per bedroom
            price_per_bedroom = int(
                prop["price"].replace(".", "").replace(" kr", "")
            ) / int(prop["bedrooms"])
            prop["price_per_bedroom"] = price_per_bedroom

            try:
                price = int(prop["price"].replace(".", "").replace(" kr", ""))
                if price < average_price:
                    under_average.append(prop)
                else:
                    over_average.append(prop)
            except (ValueError, TypeError):
                continue

        self.print_properties(under_average, "Properties Under Average Price")
        self.print_properties(over_average, "Properties Over Average Price")

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
            html_body += "<h2>Meðalfermetraverð fyrir valin svæði:</h2>"
            html_body += "<ul>"
            for bedrooms, avg_price in sorted(avg_price_per_m2.items()):
                avg_price_formatted = f"{avg_price:,}".replace(",", ".")
                html_body += f"<li><strong>{bedrooms} svefnherbergi:</strong> {avg_price_formatted} kr.</li>"
            html_body += "</ul>"
            html_body += "<hr>"

            html_body += self.generate_property_html(
                under_average, "Eignir undir meðalfermetraverði"
            )
            html_body += "<hr>"
            html_body += self.generate_property_html(
                over_average, "Eignir yfir meðalfermetraverði"
            )

            html_body += "</body></html>"

            logging.info("\nAttempting to send email notification...")
            self.send_email_notification(subject, html_body)
        else:
            logging.info("\nNo properties found. No email notification sent.")


if __name__ == "__main__":

    scraper = Scraper()
    scraper.main()