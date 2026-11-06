import os

import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.environ.get("HEALTHCHECK_URL", "")


def ping():
    if not URL:
        return
    try:
        requests.get(URL, timeout=10)
    except requests.RequestException as error:
        print(f"Heartbeat failed: {error}")
