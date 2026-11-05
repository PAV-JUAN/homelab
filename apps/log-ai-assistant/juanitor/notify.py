import os

import requests
from dotenv import load_dotenv

load_dotenv()

NTFY_URL = os.environ["NTFY_URL"]
NTFY_TOPIC = os.environ["NTFY_TOPIC"]
NTFY_TOKEN = os.environ["NTFY_TOKEN"]

PRIORITY = {"urgent": 5, "warning": 3, "resolved": 2}
TAGS = {"urgent": "rotating_light", "warning": "warning",
        "resolved": "white_check_mark"}


def send(title, message, kind):
    response = requests.post(
        NTFY_URL,
        json={
            "topic": NTFY_TOPIC,
            "title": title,
            "message": message,
            "priority": PRIORITY[kind],
            "tags": [TAGS[kind]],
        },
        headers={"Authorization": f"Bearer {NTFY_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()
