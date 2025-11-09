import time
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

import requests
import os
import json
from dotenv import load_dotenv

load_dotenv()  # Load environment variables from .env file

# --- THIS IS THE CORRECT, SECURE METHOD ---
#
# 1. Get the API Key securely from an environment variable.
#    This line will read the key you set in your terminal.
# -----------------------------------------------------------------
API_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")
TO_EMAIL = os.environ.get("TO_EMAIL")
# -----------------------------------------------------------------

# Define the file to store all properties
PROPERTIES_FILE = "properties.json"

# 2. Check if the key was actually found in the environment.
if not API_KEY:
    print("---------------------")
    print("\nERROR: SENDGRID_API_KEY environment variable not set.")
    print("You must set the variable *before* running the script.")
    print("\nIn your terminal, run this command (using your NEW key):")
    print("export SENDGRID_API_KEY='SG.Your_Now_Key_Goes_Here'")
    print("---------------------")
    # exit() # Do not exit here, let the calling script handle it

if not FROM_EMAIL:
    print("---------------------")
    print("\nERROR: FROM_EMAIL environment variable not set.")
    print("You must set the variable *before* running the script.")
    print("\nIn your terminal, run this command:")
    print("export FROM_EMAIL='your_from_email@example.com'")
    print("---------------------")
    # exit() # Do not exit here, let the calling script handle it

if not TO_EMAIL:
    print("---------------------")
    print("\nERROR: TO_EMAIL environment variable not set.")
    print("You must set the variable *before* running the script.")
    print("\nIn your terminal, run this command:")
    print("export TO_EMAIL='your_to_email@example.com'")
    print("---------------------")
    # exit() # Do not exit here, let the calling script handle it

# 3. Define the API endpoint
API_URL = "https://api.sendgrid.com/v3/mail/send"


def send_email_notification(subject, body):
    if not API_KEY or not FROM_EMAIL or not TO_EMAIL:
        print("Email sending skipped due to missing environment variables.")
        return False

    # 4. Set up the authorization headers
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    # 5. Define the email data (payload)
    data = {
        "personalizations": [{"to": [{"email": TO_EMAIL}], "subject": subject}],
        "from": {"email": FROM_EMAIL},
        "content": [{"type": "text/plain", "value": body}],
    }

    print(
        f"Attempting to send email to {data['personalizations'][0]['to'][0]['email']}..."
    )
    print(f"From: {data['from']['email']}")
    print(f"Subject: {data['personalizations'][0]['subject']}")
    print("---")

    try:
        # 6. Make the POST request
        response = requests.post(API_URL, headers=headers, data=json.dumps(data))

        # 7. Print the response information
        print("Request finished.")
        print(f"HTTP Status Code: {response.status_code}")
        print("--- Response Body ---")

        if response.text:
            try:
                print(json.dumps(response.json(), indent=2))
            except requests.exceptions.JSONDecodeError:
                print(response.text)
        else:
            print("[No response body]")

        print("---------------------")

        # 8. Explain the status code
        if response.status_code == 202:
            print("\nSUCCESS: Status code is 202 (Accepted).")
            print("This means SendGrid accepted your request with your NEW key.")
            print("Please wait 1-2 minutes and check your inbox AND spam folder.")
            return True
        elif response.status_code == 401:
            print("\nERROR: Status code is 401 (Unauthorized).")
            print("This means your NEW API Key is incorrect or you didn't set the")
            print("environment variable correctly.")
        elif response.status_code == 403:
            print("\nERROR: Status code is 403 (Forbidden).")
            print(
                "This can mean your new API key doesn't have 'Mail Send' permissions,"
            )
            print("or your 'from' address is not verified (but you already did this).")
        else:
            print(f"\nINFO: Received status code {response.status_code}.")
            print("See the response body above for more details.")
        return False

    except requests.exceptions.RequestException as e:
        print(f"An error occurred while making the request: {e}")
        return False


# The original script's direct execution is removed.
# Now, send_email_notification must be called explicitly.


def load_existing_properties():
    """Loads existing properties from properties.json."""
    if os.path.exists(PROPERTIES_FILE) and os.path.getsize(PROPERTIES_FILE) > 0:
        with open(PROPERTIES_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                print(
                    f"Warning: {PROPERTIES_FILE} is corrupted or empty. Starting with an empty list."
                )
                return []
    return []


def save_properties(properties_list):
    """Saves the current list of properties to properties.json."""
    with open(PROPERTIES_FILE, "w", encoding="utf-8") as f:
        json.dump(properties_list, f, indent=4, ensure_ascii=False)


def scrape_visir_properties():
    # 1. Define the target URL and base URL
    base_url = "https://fasteignir.visir.is"

    # Get search parameters from environment variables
    min_price = os.environ.get("MIN_PRICE")
    max_price = os.environ.get("MAX_PRICE")
    min_bedrooms = os.environ.get("MIN_BEDROOMS")
    max_bedrooms = os.environ.get("MAX_BEDROOMS")

    # Validate environment variables
    if not all([min_price, max_price, min_bedrooms, max_bedrooms]):
        print("---------------------")
        print("\nERROR: One or more search parameter environment variables (MIN_PRICE, MAX_PRICE, MIN_BEDROOMS, MAX_BEDROOMS) not set.")
        print("Please set them before running the script.")
        print("Example: export MIN_PRICE='70000000'")
        print("---------------------")
        return [] # Exit if parameters are not set

    # Construct the start URL dynamically
    start_url = (
        f"https://fasteignir.visir.is/search/results/?stype=sale#/"
        f"?zip=104,105&price={min_price},{max_price}&bedroom={min_bedrooms},{max_bedrooms}&category=2,1,4,7,17&stype=sale"
    )

    # Load ignored address substrings from config.json
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    skip_address_substrings = config.get("ignored_strings", [])

    # Load existing properties and create a set of their links for quick lookup
    existing_properties = load_existing_properties()
    existing_property_links = {prop["link"] for prop in existing_properties}

    new_properties_found_this_run = []

    # --- Selenium Setup ---
    print("Setting up Chrome driver...")
    service = Service(ChromeDriverManager().install())

    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(service=service, options=options)

    print(f"Opening browser and navigating to {start_url}...")
    driver.get(start_url)

    print("Waiting for initial page load...")
    time.sleep(5)

    # --- Pagination Loop ---
    while True:
        # --- BeautifulSoup Parsing ---
        soup = BeautifulSoup(driver.page_source, "html.parser")

        # --- Data Extraction ---
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
                # Use environment variables for price filtering
                if not int(min_price) <= price_num <= int(max_price):
                    continue
            except (ValueError, TypeError):
                # Could not convert to number, skip it
                continue

            size = size_tag.get_text(strip=True) if size_tag else "N/A"
            total_rooms = rooms_tag.get_text(strip=True) if rooms_tag else "N/A"
            bedrooms = bedrooms_tag.get_text(strip=True) if bedrooms_tag else "N/A"

            if link != "N/A" and address != "N/A":
                prop_data = {
                    "address": address,
                    "price": price_str,
                    "size_m2": size,
                    "total_rooms": total_rooms,
                    "bedrooms": bedrooms,
                    "link": link,
                }

                # Check if this property (by link) is already in our existing list
                if prop_data["link"] not in existing_property_links:
                    new_properties_found_this_run.append(prop_data)
                    existing_properties.append(
                        prop_data
                    )  # Add to the list that will be saved
                    existing_property_links.add(
                        prop_data["link"]
                    )  # Add link to set for future checks

        # --- Find and click the 'next' button ---
        try:
            # Find the 'next' button. It's an 'a' tag with class 'b-navigation-direction-next'
            # that does NOT have a 'disabled' class.
            next_button = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "a.b-navigation-direction-next:not(.disabled)")
                )
            )
            print("Found 'next' page button. Clicking it...")
            driver.execute_script("arguments[0].click();", next_button)
            # Wait for the page to reload with new properties
            time.sleep(5)
        except Exception:
            print(
                "No more 'next' page button found, or it is disabled. Ending pagination."
            )
            break  # Exit the loop if no 'next' button is found

    # --- Cleanup ---
    driver.quit()

    # Save the updated list of all properties (including new ones)
    save_properties(existing_properties)

    return new_properties_found_this_run


# --- Run the scraper ---
new_properties = scrape_visir_properties()

# Print the results
print(f"\n--- Total NEW unique properties found this run: {len(new_properties)} ---")


# Sort new_properties by price
def get_numeric_price(price_str):
    try:
        return int(price_str.replace(".", "").replace(" kr", ""))
    except (ValueError, TypeError):
        return float("inf")  # Place properties with non-numeric prices at the end


new_properties.sort(key=lambda x: get_numeric_price(x["price"]))

for i, prop in enumerate(new_properties):
    print(f"\nProperty #{i+1}")
    print(f"  Address: {prop['address']}")
    print(f"  Price: {prop['price']}")
    print(f"  Size: {prop['size_m2']}")
    print(f"  Bedrooms: {prop['bedrooms']}")
    print(f"  Link: {prop['link']}")

# --- Send email notification if new properties are found ---
if new_properties:
    subject = f"New Properties Found: {len(new_properties)} listings"
    body_lines = ["New properties matching your criteria have been found:"]
    for prop in new_properties:
        body_lines.append(f"\nAddress: {prop['address']}")
        body_lines.append(f"Price: {prop['price']}")
        body_lines.append(f"Size: {prop['size_m2']}")
        body_lines.append(f"Bedrooms: {prop['bedrooms']}")
        body_lines.append(f"Link: {prop['link']}")
    body = "\n".join(body_lines)

    print("\nAttempting to send email notification...")
    send_email_notification(subject, body)
else:
    print("\nNo new properties found. No email notification sent.")
