# Juanitor

A local AI assistant that watches my homelab server and tells me what broke and why.

## Why I built it

I mostly wanted to test and play around with LLMs, and see what a small model
can actually do in a real project.

The challenge: make it run entirely on an old recycled PC with no GPU.
No cloud, no API, everything on my own server (a 2011 Lenovo ThinkCentre,
4 cores, 10 GB RAM).

## What it does

Every 10 minutes, Juanitor checks my server and sends an alert to my phone
when something is wrong:

- **Disks:** almost full, or filling up
- **CPU:** too hot, or overloaded for a long time
- **Memory:** running out of RAM
- **Backups:** missed, failed, or can't be restored
- **Drive health (SMART):** bad sectors, connection errors, overheating
- **Docker containers:** crash loops, out-of-memory kills, crashed and stayed down
- **Security:** failed SSH logins, logins from unknown addresses, attack spikes (CrowdSec)

Each alert comes with a short explanation from the AI: what's going on,
the likely cause, and what to check next.

## How it works

1. Plain Python checks the server and finds the problem. The AI never decides
   if something is wrong, it only explains it.
2. Juanitor remembers what it already told me, so I get one alert per problem
   (plus a reminder after 24h, and a message when it's fixed).
3. When there's a new problem, it collects evidence: the biggest folders, the
   last log lines of a crashed container, the top processes, etc.
4. A small local model (Qwen 2.5 3B, through Ollama) reads the evidence and
   writes the explanation.
5. The alert goes to my phone through my own ntfy server.

If the AI is slow or gives a bad answer, the plain alert still goes out.

### What it can and can't do

**It can:**
- Read system info, logs and disk usage
- Read Docker container info and logs (through a read-only proxy)
- Write to its own small database, nothing else

**It can't:**
- Change, delete or restart anything on the server
- Run the commands it suggests. Suggestions are for me, and only read-only
  commands (`ls`, `df`, `grep`, `docker logs`...) are shown at all
- Send anything outside my network

It runs as a normal user through a systemd timer, with one extra permission:
read any file. The rest of the system is read-only to it.

## Making the AI accurate

Small models can hallucinate, so I couldn't just trust the answers. It took
several rounds of testing to get explanations I could rely on.

**How I tested it:** I wrote realistic test scenarios (a full backup drive,
an unplugged disk, a crashing container, a login from an unknown address...)
with an answer key: what a good answer must say and must never say. Each
scenario runs several times, because the model doesn't answer the same way
every time.

**What I found and fixed:**
- **It invented things.** It claimed a disk "grew significantly" with no
  history at all, and suggested a backup service that doesn't exist.
  Fix: give it real facts from the code (is the drive mounted? did the backup
  job start?) instead of letting it guess.
- **Some mistakes kept coming back.** Fix: Juanitor checks every answer. If it
  ignores a key fact, it gets sent back to be corrected. If it's still wrong,
  the code writes a safe answer itself.
- **It suggested dangerous commands,** like killing a process or restoring
  files into the backup drive. Fix: only commands from a read-only list are
  ever shown.
- **It didn't know my setup.** It wanted to kill ffmpeg, which is just
  Jellyfin playing a movie. Fix: short notes about my setup, added to the
  evidence when relevant.
- **My own tests were too easy at first.** A perfect score turned out to mean
  the answer key was too generous. Lesson: read the answers, not just the score.

Latest result: **41/42 (98%)** on 14 scenarios, 3 runs each.
See [evals/last_run.md](evals/last_run.md) for the details.

## Stack

Python, SQLite, Ollama (Qwen 2.5 3B), Docker, systemd, ntfy, Caddy, CrowdSec, restic

## License

MIT
