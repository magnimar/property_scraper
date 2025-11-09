import requests
import os
import json
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

# --- THIS IS THE CORRECT, SECURE METHOD ---
#
# 1. Get the API Key securely from an environment variable.
#    This line will read the key you set in your terminal.
# -----------------------------------------------------------------
API_KEY = os.environ.get('SENDGRID_API_KEY')
FROM_EMAIL = os.environ.get('FROM_EMAIL')
TO_EMAIL = os.environ.get('TO_EMAIL')
# -----------------------------------------------------------------


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
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    # 5. Define the email data (payload)
    data = {
        "personalizations": [
            {
                "to": [{"email": TO_EMAIL}],
                "subject": subject
            }
        ],
        "from": {"email": FROM_EMAIL},
        "content": [
            {
                "type": "text/plain", 
                "value": body
            }
        ]
    }

    print(f"Attempting to send email to {data['personalizations'][0]['to'][0]['email']}...")
    print(f"From: {data['from']['email']}")
    print(f"Subject: {data['personalizations'][0]['subject']}")
    print("---")

    try:
        # 6. Make the POST request
        response = requests.post(API_URL, headers=headers, data=json.dumps(data))

        # 7. Print the response information
        print(f"Request finished.")
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
             print("This can mean your new API key doesn't have 'Mail Send' permissions,")
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

