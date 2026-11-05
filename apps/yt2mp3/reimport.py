"""
Re-press: send every song in the old library folder back through the new converter
(full albums split into tracks, titles cleaned, songs identified and tagged), then remove the old copies.

The YouTube link is read from each old file's tags (yt-dlp saved it in the "comment" field).
Songs that fail keep their old file, so nothing is lost.

Run inside the container:   python reimport.py --check    (only lists what it found, changes nothing)
                            python reimport.py            (re-presses the "You Link" folder)
                            python reimport.py "Folder"   (any other folder inside /music)
"""
import re
import sys
import time
import uuid

from mutagen.id3 import ID3

import app

CHECK_ONLY = "--check" in sys.argv
FOLDER = [a for a in sys.argv[1:] if not a.startswith("--")]
OLD_DIR = app.LIBRARY_DIR / (FOLDER[0] if FOLDER else "You Link")
# a YouTube video id is exactly 11 characters, after "v=", "youtu.be/", "shorts/" or "live/"
ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/)([\w-]{11})(?![\w-])")


def source_url(mp3):
    try:
        tags = ID3(str(mp3))
    except Exception:  # noqa: BLE001 - unreadable file: leave it alone
        return None
    for frame in tags.values():                     # look in every tag, not just the comment
        texts = list(getattr(frame, "text", None) or []) + [getattr(frame, "url", "")]
        for text in texts:
            match = ID_RE.search(str(text))
            if match:
                return f"https://www.youtube.com/watch?v={match.group(1)}"
    return None


def main():
    if not OLD_DIR.is_dir():
        print(f"Nothing to do: {OLD_DIR} doesn't exist.")
        return

    by_url, no_url = {}, []
    for mp3 in sorted(OLD_DIR.rglob("*.mp3")):
        url = source_url(mp3)
        if url:
            by_url.setdefault(url, []).append(mp3)
        else:
            no_url.append(mp3)

    total = len(by_url)
    print(f"Found {total} YouTube links in {OLD_DIR} "
          f"({len(no_url)} files without a link will be left alone).", flush=True)
    if CHECK_ONLY:
        for url, files in by_url.items():
            print(f"{url}  <-  {', '.join(f.name for f in files)}")
        for f in no_url:
            print(f"NO LINK  {f.name}")
        return

    done = failed = 0
    for i, (url, files) in enumerate(by_url.items(), start=1):
        print(f"[{i}/{total}] {url}", flush=True)
        job_id = uuid.uuid4().hex
        with app.jobs_lock:
            app.jobs[job_id] = {"kind": "single", "status": "queued", "progress": 0, "title": None,
                                "file": None, "error": None, "created": time.time(),
                                "saved_as": None, "eta": None}
        app.run_single(job_id, url, "320", auto_add=True)
        job = app.jobs.get(job_id, {})
        if job.get("status") == "done":
            for old in files:
                old.unlink(missing_ok=True)
            done += 1
            print(f"    OK   {job.get('saved_as')}", flush=True)
        else:
            failed += 1
            print(f"    FAIL kept the old file: {job.get('error')}", flush=True)

    for folder in sorted(OLD_DIR.rglob("*"), reverse=True):   # tidy up emptied folders
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    if OLD_DIR.is_dir() and not any(OLD_DIR.iterdir()):
        OLD_DIR.rmdir()

    print(f"Finished: {done} re-pressed, {failed} failed, {len(no_url)} without a link.", flush=True)


if __name__ == "__main__":
    main()
