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

# --- Configuration ---
CONFIG_FILE = "config.json"


def load_config():
    """Loads the entire configuration from config.json."""
    if not os.path.exists(CONFIG_FILE):
        print(f"ERROR: Configuration file '{CONFIG_FILE}' not found.")
        exit()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            print(f"ERROR: Could not decode JSON from '{CONFIG_FILE}'.")
            exit()


def save_config(config):
    """Saves the entire configuration to config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# --- User-specific Setup ---
parser = argparse.ArgumentParser(description="Scrape real estate listings.")
parser.add_argument(
    "--user",
    required=True,
    help="The user running the script (e.g., 'magni', 'gabriela').",
)
args = parser.parse_args()

config = load_config()

if args.user not in config:
    print(f"ERROR: User '{args.user}' not found in '{CONFIG_FILE}'.")
    exit()

user_config = config[args.user]

# --- Email and Search Parameters ---
API_KEY = user_config.get("SENDGRID_API_KEY")
FROM_EMAIL = user_config.get("FROM_EMAIL")
TO_EMAIL = user_config.get("TO_EMAIL")
MIN_PRICE = user_config.get("MIN_PRICE")
MAX_PRICE = user_config.get("MAX_PRICE")
MIN_BEDROOMS = user_config.get("MIN_BEDROOMS")
MAX_BEDROOMS = user_config.get("MAX_BEDROOMS")
ZIP_CODES = user_config.get("ZIP_CODES")


# 3. Define the API endpoint
API_URL = "https://api.sendgrid.com/v3/mail/send"


def send_email_notification(subject, html_body):
    if not all([API_KEY, FROM_EMAIL, TO_EMAIL]):
        print(
            "Email sending skipped due to missing API_KEY, FROM_EMAIL, or TO_EMAIL in config."
        )
        return False

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    data = {
        "personalizations": [{"to": [{"email": TO_EMAIL}], "subject": subject}],
        "from": {"email": FROM_EMAIL},
        "content": [{"type": "text/html", "value": html_body}],
    }

    print(f"Attempting to send email to {TO_EMAIL}...")
    try:
        response = requests.post(API_URL, headers=headers, data=json.dumps(data))
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


def scrape_visir_properties():
    base_url = "https://fasteignir.visir.is"

    if not all([MIN_PRICE, MAX_PRICE, MIN_BEDROOMS, MAX_BEDROOMS, ZIP_CODES]):
        print("ERROR: Missing search parameters in config file.")
        return []

    zip_codes_str = ZIP_CODES
    start_url = (
        f"https://fasteignir.visir.is/search/results/?stype=sale#/"
        f"?zip={zip_codes_str}&price={MIN_PRICE},{MAX_PRICE}&bedroom={MIN_BEDROOMS},{MAX_BEDROOMS}&category=2,1,4,7,17&stype=sale"
    )

    existing_properties = user_config.get("properties", [])
    skip_address_substrings = user_config.get("ignored_strings", [])
    existing_property_links = {prop["link"] for prop in existing_properties}

    new_properties_found_this_run = []

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
                if not int(MIN_PRICE) <= price_num <= int(MAX_PRICE):
                    continue
            except (ValueError, TypeError):
                continue

            size = size_tag.get_text(strip=True) if size_tag else "N/A"
            total_rooms = rooms_tag.get_text(strip=True) if rooms_tag else "N/A"
            bedrooms = bedrooms_tag.get_text(strip=True) if bedrooms_tag else "N/A"

            price_per_m2 = None
            if size != "N/A" and price_num:
                try:
                    size_num = float(size.replace("m²", "").replace(",", "."))
                    if size_num > 0:
                        price_per_m2 = int(price_num / size_num)
                except (ValueError, TypeError):
                    pass

            if link != "N/A" and address != "N/A":
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
                if prop_data["link"] not in existing_property_links:
                    new_properties_found_this_run.append(prop_data)
                    existing_properties.append(prop_data)
                    existing_property_links.add(prop_data["link"])
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

    # Update the config object and save it
    user_config["properties"] = existing_properties
    save_config(config)

    return new_properties_found_this_run, driver


# --- Run the scraper ---
new_properties, driver = scrape_visir_properties()

# Print the results
print(f"\n--- Total NEW unique properties found this run: {len(new_properties)} ---")


# Sort new_properties by price
def get_numeric_price(price_str):
    try:
        return int(price_str.replace(".", "").replace(" kr", ""))
    except (ValueError, TypeError):
        return float("inf")  # Place properties with non-numeric prices at the end


new_properties.sort(key=lambda x: get_numeric_price(x["price"]))

print("\nChecking for balcony and terrace information...")
for prop in new_properties:
    try:
        driver.get(prop["link"])
        time.sleep(2)
        page_text = driver.page_source.lower()
        prop["has_balcony"] = "svalir" in page_text
        prop["has_terrace"] = "sérafnota" in page_text or "garð" in page_text
    except Exception as e:
        print(f"Error checking features for {prop['address']}: {e}")
        prop["has_balcony"] = False
        prop["has_terrace"] = False

if driver:
    driver.quit()

for i, prop in enumerate(new_properties):
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

# --- Send email notification if new properties are found ---
if new_properties:
    subject = f"New Properties Found: {len(new_properties)} listings"
    html_body = "<html><body><h2>New properties matching your criteria have been found:</h2>"
    for prop in new_properties:
        html_body += f"<div style='margin-bottom: 30px; padding: 15px; border: 1px solid #ddd;'>"
        html_body += f"<h3>{prop['address']}</h3>"
        html_body += f"<p><strong>Price:</strong> {prop['price']}</p>"
        html_body += f"<p><strong>Size:</strong> {prop['size_m2']}</p>"
        if prop.get('price_per_m2'):
            price_per_m2_formatted = f"{prop['price_per_m2']:,}".replace(",", ".")
            html_body += f"<p><strong>Price per m²:</strong> {price_per_m2_formatted} kr.</p>"
        html_body += f"<p><strong>Bedrooms:</strong> {prop['bedrooms']}</p>"
        if prop.get('has_balcony') is not None:
            html_body += f"<p><strong>Balcony:</strong> {'yes' if prop['has_balcony'] else 'no'}</p>"
        if prop.get('has_terrace') is not None:
            html_body += f"<p><strong>Terrace:</strong> {'yes' if prop['has_terrace'] else 'no'}</p>"
        if prop.get('image_url'):
            html_body += f"<img src='{prop['image_url']}' alt='Property image' style='max-width: 600px; height: auto; margin: 10px 0;' />"
        html_body += f"<p><a href='{prop['link']}'>View Property</a></p>"
        html_body += "</div>"
    html_body += "</body></html>"

    print("\nAttempting to send email notification...")
    send_email_notification(subject, html_body)
else:
    print("\nNo new properties found. No email notification sent.")
