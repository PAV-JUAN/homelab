import os

import requests
from dotenv import load_dotenv

load_dotenv()

URL = os.environ.get("JELLYFIN_URL", "http://127.0.0.1:8096")
KEY = os.environ.get("JELLYFIN_API_KEY", "")


def active_streams():
    if not KEY:
        return 0
    try:
        response = requests.get(
            f"{URL}/Sessions",
            headers={"Authorization": f'MediaBrowser Token="{KEY}"'},
            timeout=5,
        )
        response.raise_for_status()
    except requests.RequestException:
        return 0
    return sum(1 for session in response.json()
               if session.get("NowPlayingItem")
               and not session.get("PlayState", {}).get("IsPaused"))
