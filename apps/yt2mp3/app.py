"""
You Link to MP3 - a small self-hosted YouTube -> MP3 converter.
Uses yt-dlp for downloading and ffmpeg for audio extraction.
Before a song reaches the Navidrome library it is cleaned up and identified:
  - "Full Album" videos are split into real tracks using the video's chapters
  - titles are cleaned ("(Official Video)", "[HD]"...) and "Artist - Title" is split
  - beets fingerprints the audio (AcoustID), looks it up in MusicBrainz and writes the real tags,
    adds a genre from Last.fm, and files it as Artist/Album/01 Title.mp3
  - songs beets isn't sure about keep the cleaned-up YouTube tags (nothing is guessed)
"""
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, render_template, request, send_file, abort
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "/data"))
LIBRARY_DIR = Path(os.environ.get("LIBRARY_DIR", "/music"))
LIBRARY_SUBDIR = os.environ.get("LIBRARY_SUBDIR", "Library")        # folder inside the music library
LIBRARY_ROOT = LIBRARY_DIR / LIBRARY_SUBDIR
AUTO_ADD = os.environ.get("AUTO_ADD_TO_LIBRARY", "false").lower() in ("1", "true", "yes")
AUTO_TAG = os.environ.get("AUTO_TAG", "true").lower() in ("1", "true", "yes")
NAVIDROME_URL = os.environ.get("NAVIDROME_URL", "")                 # optional; page guesses :4533 otherwise
PUID = int(os.environ.get("PUID", 1000))                            # owner of files added to the library
PGID = int(os.environ.get("PGID", 1000))
MAX_DURATION = int(os.environ.get("MAX_DURATION_SECONDS", 3 * 60 * 60))  # 3h default
MAX_PLAYLIST_ITEMS = int(os.environ.get("MAX_PLAYLIST_ITEMS", 100))
FILE_TTL = int(os.environ.get("FILE_TTL_SECONDS", 60 * 60))              # keep temp files 1h
FULL_ALBUM_MIN = int(os.environ.get("FULL_ALBUM_MIN_SECONDS", 15 * 60))   # long + chapters = an album
DEFAULT_BITRATE = os.environ.get("DEFAULT_BITRATE", "192")
ALLOWED_BITRATES = {"128", "192", "256", "320"}
BEETS_DIR = DOWNLOAD_DIR / "_beets"         # beets' database and log; "_" keeps it safe from cleanup
BEETS_CONFIG = BEETS_DIR / "config.yaml"

YOUTUBE_RE = re.compile(
    r"^(https?://)?(www\.|m\.|music\.)?"
    r"(youtube\.com/(watch\?v=[\w\-]{6,}|shorts/[\w\-]{6,}|live/[\w\-]{6,}|playlist\?list=[\w\-]{2,})"
    r"|youtu\.be/[\w\-]{6,})",
    re.IGNORECASE,
)

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
library_lock = threading.Lock()


def update_job(job_id: str, **fields):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def clean_error(e: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))  # strip ANSI colors from yt-dlp errors
    return msg.replace("ERROR: ", "")[:500]


def playlist_id(url: str) -> str | None:
    if not url.startswith("http"):
        url = "https://" + url
    return (parse_qs(urlparse(url).query).get("list") or [None])[0]


def safe_folder_name(name: str, fallback: str = "Unknown") -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


# ---------- reading titles ----------

# "(Official Video)", "[HD]", "(Lyrics)", "(Full Album)", "(2011 Remaster)"...
JUNK_RE = re.compile(
    r"\s*[\(\[\{【]\s*(?:"
    r"official(?:\s+(?:music|lyric|hd))?(?:\s+(?:video|audio|visuali[sz]er))?"
    r"|(?:music|lyric)\s+video|lyrics?|audio|video|visuali[sz]er|hd|hq|4k|explicit"
    r"|full\s+(?:album|ep)|remaster(?:ed)?(?:\s+\d{4})?|\d{4}\s+remaster(?:ed)?"
    r")\s*[\)\]\}】]",
    re.IGNORECASE,
)
TRAILING_JUNK_RE = re.compile(
    r"(?:\s*[-–—|]\s*|\s+)(?:full\s+(?:album|ep)|official\s+(?:music\s+)?(?:video|audio)|hd|hq|4k|lyrics?)\s*$", re.IGNORECASE
)
SEPARATOR_RE = re.compile(r"\s+[-–—|]\s+")                             # "Artist - Title"
TRACK_PREFIX_RE = re.compile(r"^\s*(?:\(\d{1,3}\)|\d{1,3}\s*[.):\-–—])\s*")  # "01. ", "1 - ", "(1) "
CREDIT_RE = re.compile(r"\s*[\(\[]\s*prod(?:\.|uced)?\s*(?:by\s*)?[^\)\]]*[\)\]]", re.IGNORECASE)  # "(Prod. X)"
TAG_END_RE = re.compile(r"\s*\[[^\]]{1,15}\]\s*$")                                       # "[MAD]", "[AMV]"
NON_GENRES = {"music", "entertainment", "people & blogs", "film & animation", "gaming"}


def clean_text(text: str) -> str:
    text = JUNK_RE.sub("", text or "")
    text = CREDIT_RE.sub("", text)
    text = TRAILING_JUNK_RE.sub("", text)
    text = TAG_END_RE.sub("", text)
    text = re.sub(r"(^|\s)#(?=\w)", r"\1", text)                                          # "#Kroi" -> "Kroi"
    return re.sub(r"\s{2,}", " ", text).strip(" -–—|·")


def split_on_dash(text: str) -> tuple[str | None, str]:
    parts = SEPARATOR_RE.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None, text.strip()


def channel_artist(info: dict) -> str:
    name = info.get("uploader") or info.get("channel") or "Unknown Artist"
    return re.sub(r"\s*-\s*Topic$", "", name).strip() or "Unknown Artist"   # "ALI - Topic" -> "ALI"


def artist_and_name(text: str, info: dict) -> tuple[str, str]:
    """'Artist - Title' -> (artist, title). Swaps when the video is titled 'Title - Artist'."""
    left, right = split_on_dash(clean_text(text))
    channel = channel_artist(info)
    if left is None:
        return channel, right
    if right.lower() == channel.lower() and left.lower() != channel.lower():
        return right, left
    return left, right


def song_guess(info: dict) -> dict:
    year = str(info.get("release_year") or "")
    if info.get("track") and info.get("artist"):       # YouTube Music gives real fields
        title = clean_text(info["track"])
        return {"artist": info["artist"], "title": title, "album": info.get("album") or title, "year": year}
    artist, title = artist_and_name(info.get("title") or "audio", info)
    return {"artist": artist, "title": title, "album": title, "year": year}


def chapter_title(raw: str, artist: str) -> str:
    text = clean_text(TRACK_PREFIX_RE.sub("", raw or ""))
    left, right = split_on_dash(text)
    if left and left.lower() == artist.lower():
        return right
    if left and right.lower() == artist.lower():
        return left
    return text or "Untitled"


def write_tags(path: Path, artist: str, title: str, album: str = "", albumartist: str = "",
               track: int | None = None, year: str = ""):
    try:
        tags = EasyID3(str(path))
    except ID3NoHeaderError:
        tags = EasyID3()
    tags["artist"] = artist
    tags["title"] = title
    tags["album"] = album or title
    tags["albumartist"] = albumartist or artist
    if track:
        tags["tracknumber"] = str(track)
    if year:
        tags["date"] = year
    if (tags.get("genre") or [""])[0].lower() in NON_GENRES:      # YouTube's category, not a genre
        del tags["genre"]
    tags.save(str(path))


# ---------- library ----------

def library_available() -> bool:
    return LIBRARY_DIR.is_dir() and os.access(LIBRARY_DIR, os.W_OK)


def _chown(path: Path):
    try:
        os.chown(path, PUID, PGID)
    except (PermissionError, OSError):
        pass  # not running as root; file keeps the container user's ownership


def fix_ownership():
    for root, _dirs, files in os.walk(LIBRARY_ROOT):
        _chown(Path(root))
        for f in files:
            _chown(Path(root) / f)


def write_beets_config():
    BEETS_DIR.mkdir(parents=True, exist_ok=True)
    BEETS_CONFIG.write_text(f"""directory: "{LIBRARY_ROOT}"
library: "{BEETS_DIR / 'library.db'}"
plugins: musicbrainz chroma fetchart embedart lastgenre
import:
  move: yes
  write: yes
  quiet: yes
  quiet_fallback: asis
  duplicate_action: keep
  log: "{BEETS_DIR / 'import.log'}"
match:
  strong_rec_thresh: 0.10
  distance_weights:
    data_source: 0.0
paths:
  default: $albumartist/$album%aunique{{}}/$track $title
  singleton: $artist/Singles/$title
  comp: Compilations/$album%aunique{{}}/$track $title
chroma:
  auto: yes
lastgenre:
  auto: yes
  canonical: yes
  count: 1
  force: yes
fetchart:
  auto: yes
embedart:
  auto: yes
  remove_art_file: yes
""")


def beets_import(target: Path, singleton: bool) -> bool:
    cmd = ["beet", "-c", str(BEETS_CONFIG), "import", "-q"]
    if singleton:
        cmd.append("-s")
    cmd.append(str(target))
    try:
        result = subprocess.run(cmd, env={**os.environ, "BEETSDIR": str(BEETS_DIR)},
                                capture_output=True, text=True, timeout=1200)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _first(tags, key: str) -> str:
    values = tags.get(key)
    return values[0] if values else ""


def file_by_tags(mp3: Path) -> Path:
    """Fallback filing (beets off or it left the file behind): Artist/Album/01 Title.mp3 from our tags."""
    tags = EasyID3(str(mp3))
    artist = _first(tags, "albumartist") or _first(tags, "artist") or "Unknown Artist"
    album = _first(tags, "album") or "Singles"
    title = _first(tags, "title") or mp3.stem
    track = _first(tags, "tracknumber").split("/")[0]
    folder = LIBRARY_ROOT / safe_folder_name(artist) / safe_folder_name(album)
    folder.mkdir(parents=True, exist_ok=True)
    name = safe_folder_name(f"{int(track):02d} {title}" if track.isdigit() else title)
    dest = folder / f"{name}.mp3"
    n = 1
    while dest.exists():  # never overwrite: "Song.mp3" -> "Song (1).mp3"
        dest = folder / f"{name} ({n}).mp3"
        n += 1
    shutil.move(str(mp3), dest)
    return dest


def add_to_library(src: Path, album: bool = False):
    """Identify, tag and file one song (or a folder of an album's tracks) into the library."""
    with library_lock:
        LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        if AUTO_TAG:
            beets_import(src, singleton=not album)
        if src.is_file():
            leftovers = [src]
        elif src.is_dir():
            leftovers = sorted(src.glob("*.mp3"))
        else:
            leftovers = []
        for mp3 in leftovers:
            file_by_tags(mp3)
        fix_ownership()


# ---------- yt-dlp ----------

def build_opts(out_dir: Path, bitrate: str, progress_hook, split: bool = False):
    def duration_filter(info, *, incomplete):
        duration = info.get("duration")
        if duration and duration > MAX_DURATION:
            return f"Too long ({duration // 60} min, limit is {MAX_DURATION // 60} min)."
        return None

    postprocessors = [
        {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": bitrate},
        {"key": "FFmpegMetadata", "add_metadata": True},
        {"key": "EmbedThumbnail"},
    ]
    outtmpl = {"default": str(out_dir / "%(title).150s.%(ext)s")}
    if split:
        postprocessors.append({"key": "FFmpegSplitChapters", "force_keyframes": False})
        outtmpl["chapter"] = str(out_dir / "tracks" / "%(section_number)03d.%(ext)s")
    return {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "windowsfilenames": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [progress_hook],
        "match_filter": duration_filter,
        "writethumbnail": True,
        "postprocessors": postprocessors,
    }


def probe(url: str) -> dict:
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Could not read video info.")
    return info


def is_full_album(info: dict) -> bool:
    chapters = info.get("chapters") or []
    long_enough = (info.get("duration") or 0) >= FULL_ALBUM_MIN
    says_album = re.search(r"full\s+(album|ep)", info.get("title") or "", re.IGNORECASE)
    return len(chapters) >= 3 and bool(long_enough or says_album)


def download_one(url: str, out_dir: Path, bitrate: str, progress_hook) -> tuple[Path, dict]:
    """Download + convert a single video. Returns (mp3 path, info). Raises on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(build_opts(out_dir, bitrate, progress_hook)) as ydl:
        info = ydl.extract_info(url, download=True)
    if info is None:
        raise RuntimeError("Could not read video info.")
    mp3s = list(out_dir.glob("*.mp3"))
    if not mp3s:
        raise RuntimeError(f"Too long (limit is {MAX_DURATION // 60} min)."
                           if (info.get("duration") or 0) > MAX_DURATION
                           else "No MP3 was produced.")
    return mp3s[0], info


def download_album(url: str, out_dir: Path, bitrate: str, progress_hook):
    """Download a full-album video and split it into tracks by its chapters. Returns (folder, artist, album, count)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(build_opts(out_dir, bitrate, progress_hook, split=True)) as ydl:
        info = ydl.extract_info(url, download=True)
    if info is None:
        raise RuntimeError("Could not read video info.")
    tracks_dir = out_dir / "tracks"
    tracks = sorted(tracks_dir.glob("*.mp3"))
    if not tracks:
        raise RuntimeError("Couldn't split this album into tracks.")
    for full in out_dir.glob("*.mp3"):          # the full-length file; only the tracks are kept
        full.unlink()
    artist, album = artist_and_name(info.get("title") or "Album", info)
    year = str(info.get("release_year") or "")
    chapters = info.get("chapters") or []
    for n, path in enumerate(tracks, start=1):
        raw = chapters[n - 1].get("title") if n - 1 < len(chapters) else f"Track {n}"
        write_tags(path, artist, chapter_title(raw, artist), album, artist, n, year)
    return tracks_dir, artist, album, len(tracks)


def tag_single(mp3: Path, info: dict) -> tuple[Path, str]:
    """Write cleaned-up tags to a single song and give the file a readable name."""
    g = song_guess(info)
    write_tags(mp3, g["artist"], g["title"], g["album"], g["artist"], None, g["year"])
    nicer = mp3.with_name(safe_folder_name(f"{g['artist']} - {g['title']}", "audio") + ".mp3")
    if nicer != mp3 and not nicer.exists():
        mp3 = mp3.rename(nicer)
    return mp3, f"{g['artist']} – {g['title']}"


def make_progress_hook(job_id: str, on_pct=None):
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            pct = round(done / total * 100, 1) if total else 0
            eta = d.get("eta")
            update_job(job_id, status="downloading", progress=pct,
                       eta=int(eta) if isinstance(eta, (int, float)) else None)
            if on_pct:
                on_pct(pct)
        elif d["status"] == "finished":
            update_job(job_id, status="converting", progress=100, eta=None)
    return hook


# ---------- jobs ----------

def run_single(job_id: str, url: str, bitrate: str, auto_add: bool = False):
    job_dir = DOWNLOAD_DIR / job_id
    keep_dir = False
    try:
        hook = make_progress_hook(job_id)
        info = probe(url)
        if is_full_album(info):
            if not library_available():
                raise RuntimeError("Full albums are split into tracks and go straight to the library, "
                                   "but the library folder isn't set up on the server.")
            update_job(job_id, title=info.get("title"))
            tracks_dir, artist, album, count = download_album(url, job_dir, bitrate, hook)
            update_job(job_id, status="converting", progress=100)
            add_to_library(tracks_dir, album=True)
            update_job(job_id, status="done", progress=100, title=f"{artist} – {album}", file=None,
                       saved_as=f"{album} ({count} tracks)")
            return
        mp3, info = download_one(url, job_dir, bitrate, hook)
        mp3, label = tag_single(mp3, info)
        if (AUTO_ADD or auto_add) and library_available():
            update_job(job_id, status="converting", progress=100)
            add_to_library(mp3)
            update_job(job_id, status="done", progress=100, title=label, file=None, saved_as=label)
        else:
            keep_dir = True  # the file waits here for the download or "add to library" button
            update_job(job_id, status="done", progress=100, title=label, file=str(mp3))
    except Exception as e:  # noqa: BLE001 - report any failure to the UI
        update_job(job_id, status="error", error=clean_error(e))
    finally:
        if not keep_dir:
            shutil.rmtree(job_dir, ignore_errors=True)


def run_playlist(job_id: str, url: str, bitrate: str):
    job_dir = DOWNLOAD_DIR / job_id
    try:
        list_id = playlist_id(url)
        list_url = f"https://www.youtube.com/playlist?list={list_id}" if list_id else url
        flat_opts = {"extract_flat": "in_playlist", "playlistend": MAX_PLAYLIST_ITEMS,
                     "quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(flat_opts) as ydl:
            info = ydl.extract_info(list_url, download=False)
        entries = [e for e in (info or {}).get("entries") or [] if e and e.get("id")]
        entries = entries[:MAX_PLAYLIST_ITEMS]
        if not entries:
            raise RuntimeError("That playlist is empty, private, or couldn't be read.")

        name = (info.get("title") or "Playlist").strip()
        total = len(entries)
        update_job(job_id, playlist_title=name, folder=name, total=total, title=name)

        added, skipped = 0, []
        started = time.time()
        for i, entry in enumerate(entries, start=1):
            song_title = entry.get("title") or f"Song {i}"
            update_job(job_id, status="downloading", index=i, current_title=song_title,
                       progress=0, eta=None, overall=round((i - 1) / total * 100, 1),
                       overall_eta=_overall_eta(started, i - 1, total))

            def on_pct(pct, i=i):
                update_job(job_id, overall=round((i - 1 + pct / 100) / total * 100, 1))

            song_dir = job_dir / str(i)
            try:
                mp3, song_info = download_one(
                    f"https://www.youtube.com/watch?v={entry['id']}", song_dir, bitrate,
                    make_progress_hook(job_id, on_pct))
                mp3, label = tag_single(mp3, song_info)
                add_to_library(mp3)
                added += 1
                update_job(job_id, added=added, current_title=label)
            except Exception as e:  # noqa: BLE001 - skip this song, keep going
                skipped.append({"title": song_title, "reason": clean_error(e)})
                update_job(job_id, skipped=skipped)
            finally:
                shutil.rmtree(song_dir, ignore_errors=True)

        if added == 0:
            raise RuntimeError("None of the songs in this playlist could be converted.")
        update_job(job_id, status="done", progress=100, overall=100, eta=None, overall_eta=None,
                   saved_as=name, added=added, skipped=skipped)
    except Exception as e:  # noqa: BLE001 - report any failure to the UI
        update_job(job_id, status="error", error=clean_error(e))
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)


def _overall_eta(started: float, finished: int, total: int):
    if finished == 0:
        return None
    per_song = (time.time() - started) / finished
    return int(per_song * (total - finished))


def cleanup_loop():
    """Delete finished jobs and their temp files after FILE_TTL seconds (library files are kept)."""
    while True:
        now = time.time()
        expired = []
        with jobs_lock:
            for jid, job in list(jobs.items()):
                if now - job["created"] > FILE_TTL and job["status"] in ("done", "error"):
                    expired.append(jid)
                    del jobs[jid]
        for jid in expired:
            shutil.rmtree(DOWNLOAD_DIR / jid, ignore_errors=True)
        for d in DOWNLOAD_DIR.iterdir():   # orphaned job folders (e.g. after a restart); "_" folders are kept
            if (d.is_dir() and not d.name.startswith("_") and now - d.stat().st_mtime > FILE_TTL
                    and d.name not in jobs):
                shutil.rmtree(d, ignore_errors=True)
        time.sleep(300)


# ---------- routes ----------

@app.get("/")
def index():
    return render_template("index.html", default_bitrate=DEFAULT_BITRATE,
                           library=library_available(), navidrome_url=NAVIDROME_URL,
                           max_playlist=MAX_PLAYLIST_ITEMS)


@app.post("/api/convert")
def convert():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    bitrate = str(data.get("bitrate") or DEFAULT_BITRATE)
    mode = data.get("mode") or "single"

    if not YOUTUBE_RE.match(url):
        return jsonify(error="Please enter a valid YouTube video or playlist link."), 400
    if bitrate not in ALLOWED_BITRATES:
        bitrate = DEFAULT_BITRATE

    if "/playlist" in url.lower():
        mode = "playlist"
    if mode == "playlist":
        if not playlist_id(url):
            return jsonify(error="That link doesn't contain a playlist."), 400
        if not library_available():
            return jsonify(error="Playlists go straight to the music library, "
                                 "and the library folder isn't set up on the server."), 503

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"kind": mode, "status": "queued", "progress": 0, "title": None, "file": None,
                        "error": None, "created": time.time(), "saved_as": None, "eta": None}
        if mode == "playlist":
            jobs[job_id].update(index=0, total=None, current_title=None, overall=0,
                                overall_eta=None, added=0, skipped=[], folder=None)

    target = run_playlist if mode == "playlist" else run_single
    threading.Thread(target=target, args=(job_id, url, bitrate), daemon=True).start()
    return jsonify(job_id=job_id, mode=mode), 202


@app.get("/api/status/<job_id>")
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(error="Job not found or expired."), 404
        return jsonify({k: v for k, v in job.items() if k != "file"})


@app.post("/api/library/<job_id>")
def save_to_library(job_id):
    if not library_available():
        return jsonify(error="The music library folder isn't set up on the server."), 503
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job["status"] != "done":
            return jsonify(error="This song isn't ready or has expired. Convert it again."), 404
        if job.get("saved_as"):
            return jsonify(saved_as=job["saved_as"])
        path = Path(job["file"]) if job["file"] else None
        label = job.get("title") or (path.stem if path else "song")
        job["file"] = None  # claim it so a double-click can't move it twice
    if not path or not path.exists():
        return jsonify(error="The converted file is gone. Convert it again."), 404
    try:
        add_to_library(path)
    except OSError as e:
        update_job(job_id, file=str(path))
        return jsonify(error=f"Couldn't add it to the library: {e.strerror or e}"), 500
    update_job(job_id, saved_as=label)
    return jsonify(saved_as=label)


@app.get("/api/download/<job_id>")
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job["status"] != "done" or not job["file"]:
        abort(404)
    path = Path(job["file"])
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="audio/mpeg")


@app.get("/health")
def health():
    return {"ok": True}


write_beets_config()
threading.Thread(target=cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
