import argparse
import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service

import requests
import os
import json

class Scraper:

    def __init__(self):
        self.config_file = "config.json"
        self.api_url = "https://api.sendgrid.com/v3/mail/send"
        
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
            print(f"ERROR: User '{self.args.user}' not found in '{self.config_file}'.")
            exit()

        self.user_config = config[self.args.user]

        self.API_KEY = self.user_config.get("SENDGRID_API_KEY")
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
            print(f"ERROR: Configuration file '{self.config_file}' not found.")
            exit()
        with open(self.config_file, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print(f"ERROR: Could not decode JSON from '{self.config_file}'.")
                exit()
    
    
        
    def send_email_notification(self, subject, html_body):
        if not all([self.API_KEY, self.FROM_EMAIL, self.TO_EMAIL]):
            print(
                "Email sending skipped due to missing API_KEY, FROM_EMAIL, or TO_EMAIL in config."
            )
            return False

        headers = {"Authorization": f"Bearer {self.API_KEY}", "Content-Type": "application/json"}
        data = {
            "personalizations": [{"to": [{"email": self.TO_EMAIL}], "subject": subject}],
            "from": {"email": self.FROM_EMAIL},
            "content": [{"type": "text/html", "value": html_body}],
        }

        print(f"Attempting to send email to {self.TO_EMAIL}...")
        try:
            response = requests.post(self.api_url, headers=headers, data=json.dumps(data))
            print(f"HTTP Status Code: {response.status_code}")
            if response.status_code == 202:
                print("SUCCESS: Email sent.")
                return True
            else:
                print(f"ERROR: Failed to send email. Response: {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"An error occurred while sending email: {e}")
            return False
    
    def scrape_visir_properties(self):
        base_url = "https://fasteignir.visir.is"

        if not all([self.MIN_PRICE, self.MAX_PRICE, self.MIN_BEDROOMS, self.MAX_BEDROOMS, self.ZIP_CODES]):
            print("ERROR: Missing search parameters in config file.")
            return [], None

        zip_codes_str = self.ZIP_CODES
        start_url = (
            f"https://fasteignir.visir.is/search/results/?stype=sale#/"
            f"?zip={zip_codes_str}&price={self.MIN_PRICE},{self.MAX_PRICE}&bedroom={self.MIN_BEDROOMS},{self.MAX_BEDROOMS}&category=2,1,4,7,17&stype=sale"
        )

        skip_address_substrings = self.user_config.get("ignored_strings", [])
        
        new_properties_found_this_run = []
        processed_links = set()

        driver = None

        print("Setting up Chrome driver...")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")

        if os.path.exists("/usr/bin/chromedriver"):
            service = Service(executable_path="/usr/bin/chromedriver")
            options.binary_location = "/usr/bin/chromium"
        else:
            service = Service()

        while True:
            try:
                driver = webdriver.Chrome(service=service, options=options)

                print("Successfully started chrome")

                break

            except Exception:
                print("Failed to start chrome, waiting and trying again")
                time.sleep(5)

        print(f"Opening browser and navigating to {start_url}...")
        driver.get(start_url)
        time.sleep(5)

        while True:
            soup = BeautifulSoup(driver.page_source, "html.parser")
            property_cards = soup.find_all(
                "div", class_=lambda c: c and "estate__item" in c
            )
            print(f"Found {len(property_cards)} properties on the current page.")

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
                    
                    prop_data = {
                        "address": address,
                        "price": price_str,
                        "size_m2": size,
                        "price_per_m2": price_per_m2,
                        "total_rooms": total_rooms,
                        "bedrooms": bedrooms,
                        "link": link,
                        "image_url": image_url,
                    }
                    new_properties_found_this_run.append(prop_data)
            try:
                next_button = WebDriverWait(driver, 5).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "a.b-navigation-direction-next:not(.disabled)")
                    )
                )
                driver.execute_script("arguments[0].click();", next_button)
                time.sleep(5)
            except Exception:
                break

        return new_properties_found_this_run, driver

    def main(self):
        # --- Run the scraper ---
        new_properties, driver = self.scrape_visir_properties()

        # Print the results
        print(f"\n--- Total NEW unique properties found this run: {len(new_properties)} ---")


        # Sort new_properties by price
        def get_numeric_price(price_str):
            try:
                return int(price_str.replace(".", "").replace(" kr", ""))
            except (ValueError, TypeError):
                return float("inf")  # Place properties with non-numeric prices at the end


        new_properties.sort(key=lambda x: get_numeric_price(x["price"]))

        print("\nChecking for balcony, terrace, and image information...")
        for prop in new_properties:
            needs_check = (prop.get('has_balcony') is None or 
                           prop.get('has_terrace') is None or 
                           not prop.get('image_url'))

            if needs_check and driver:
                try:
                    driver.get(prop["link"])
                    time.sleep(2)
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    page_text = driver.page_source.lower()

                    if prop.get('has_balcony') is None:
                        prop["has_balcony"] = "svalir" in page_text
                    if prop.get('has_terrace') is None:
                        prop["has_terrace"] = "sérafnota" in page_text or "garð" in page_text

                    if not prop.get('image_url'):
                        img_tag = soup.find("img", class_=lambda c: c and ("property" in str(c).lower() or "main" in str(c).lower() or "hero" in str(c).lower()))
                        if not img_tag:
                            img_tag = soup.find("img", src=lambda s: s and ("property" in s.lower() or "estate" in s.lower() or "fasteignir" in s.lower()))
                        if not img_tag:
                            img_tag = soup.find("img", src=True)

                        if img_tag and img_tag.get("src"):
                            image_url = img_tag["src"]
                            if not image_url.startswith("http"):
                                image_url = urljoin(prop["link"], image_url)
                            prop["image_url"] = image_url
                except Exception as e:
                    print(f"Error checking features for {prop['address']}: {e}")
                    if prop.get('has_balcony') is None:
                        prop["has_balcony"] = False
                    if prop.get('has_terrace') is None:
                        prop["has_terrace"] = False

        if driver:
            driver.quit()

        # --- Calculate average price ---
        total_price = 0
        property_count = 0
        for prop in new_properties:
            try:
                price = int(prop['price'].replace(".", "").replace(" kr", ""))
                total_price += price
                property_count += 1
            except (ValueError, TypeError):
                continue
        
        average_price = total_price / property_count if property_count > 0 else 0

        # --- Split properties into under and over average ---
        under_average = []
        over_average = []
        for prop in new_properties:
            try:
                price = int(prop['price'].replace(".", "").replace(" kr", ""))
                if price < average_price:
                    under_average.append(prop)
                else:
                    over_average.append(prop)
            except (ValueError, TypeError):
                continue

        def print_properties(properties, title):
            print(f"\n--- {title} ---")
            for i, prop in enumerate(properties):
                print(f"\nProperty #{i+1}")
                print(f"  Address: {prop['address']}")
                print(f"  Price: {prop['price']}")
                print(f"  Size: {prop['size_m2']}")
                if prop.get('price_per_m2'):
                    price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                    print(f"  Price per m²: {price_per_m2_formatted} kr.")
                print(f"  Bedrooms: {prop['bedrooms']}")
                if prop.get('has_balcony') is not None:
                    print(f"  Balcony: {'yes' if prop['has_balcony'] else 'no'}")
                if prop.get('has_terrace') is not None:
                    print(f"  Terrace: {'yes' if prop['has_terrace'] else 'no'}")
                print(f"  Link: {prop['link']}")

        print_properties(under_average, "Properties Under Average Price")
        print_properties(over_average, "Properties Over Average Price")

        # --- Send email notification with all properties ---
        if new_properties:
            subject = f"Properties Found: {len(new_properties)} listings"
            
            # --- Calculate average price per square meter ---
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
                    avg_price_per_m2[bedrooms] = int(total_price / bedroom_counts[bedrooms])

            # --- Construct the email body ---
            html_body = "<html><body>"
            html_body += "<h2>Average Price per Square Meter:</h2>"
            html_body += "<ul>"
            for bedrooms, avg_price in sorted(avg_price_per_m2.items()):
                avg_price_formatted = f"{avg_price:,}".replace(",", ".")
                html_body += f"<li><strong>{bedrooms} bedrooms:</strong> {avg_price_formatted} kr.</li>"
            html_body += "</ul>"
            html_body += "<hr>"

            def generate_property_html(properties, title):
                html = f"<h2>{title}</h2>"
                for prop in properties:
                    html += f"<div style='margin-bottom: 30px; padding: 15px; border: 1px solid #ddd;'>"
                    html += f"<h3>{prop['address']}</h3>"
                    html += f"<p><strong>Price:</strong> {prop['price']}</p>"
                    html += f"<p><strong>Size:</strong> {prop['size_m2']}</p>"
                    if prop.get('price_per_m2'):
                        price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
                        html += f"<p><strong>Price per m²:</strong> {price_per_m2_formatted} kr.</p>"
                    html += f"<p><strong>Bedrooms:</strong> {prop['bedrooms']}</p>"
                    if prop.get('has_balcony') is not None:
                        html += f"<p><strong>Balcony:</strong> {'yes' if prop['has_balcony'] else 'no'}</p>"
                    if prop.get('has_terrace') is not None:
                        html += f"<p><strong>Terrace:</strong> {'yes' if prop['has_terrace'] else 'no'}</p>"
                    if prop.get('image_url'):
                        html += f"<img src='{prop['image_url']}' alt='Property image' style='max-width: 600px; height: auto; margin: 10px 0;' />"
                    html += f"<p><a href='{prop['link']}'>View Property</a></p>"
                    html += "</div>"
                return html

            html_body += generate_property_html(under_average, "Properties Under Average Price")
            html_body += "<hr>"
            html_body += generate_property_html(over_average, "Properties Over Average Price")
            
            html_body += "</body></html>"

            print("\nAttempting to send email notification...")
            self.send_email_notification(subject, html_body)
        else:
            print("\nNo properties found. No email notification sent.")

if __name__ == "__main__":

    scraper = Scraper()
    scraper.main()
