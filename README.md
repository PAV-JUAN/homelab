# homelab

My home server, juanito. It's an Ubuntu machine running around 15 apps in Docker: some I use every day (music, movies, passwords), some I built myself, and some I set up mostly to learn how they work.

Everything in `apps/` is the real configuration the server is running right now. I don't write it by hand. `collect.sh` copies it from the live server, so this repo shows what's actually there, not what I meant to set up.

## Why I have this

It started small, with a little YouTube-to-MP3 app. Then I wanted an alternative to Spotify, which keeps raising its price while the app gets worse with every update, so I set up Navidrome to stream my own music from anywhere. Then I wanted my movies too, then a dashboard to see everything in one place, and it kept growing from there.

It's also where I practice the things I don't get to do much at my day job in IT support: Linux, networking, security, backups, and fixing things I broke myself.

## What runs on it

| App | What it's for | Who can reach it |
|---|---|---|
| Home Base | My own dashboard (I built it): every app, whether it's up, server stats, an Amsterdam clock | Home + Tailscale |
| Jellyfin | Movies and shows | Public (movies.ricamivida.com) |
| Fare Watch | My flight price tracker's website, see [flight-tracker](https://github.com/PAV-JUAN/flight-tracker) | Public (flights.ricamivida.com) |
| Navidrome | My music library, streamed to my phone | Home + Tailscale |
| You Link to MP3 | I built it: paste a YouTube link, get the song | Home + Tailscale |
| File Browser | Upload and manage media from any device | Home + Tailscale |
| qBittorrent + Gluetun | Download client, forced through a VPN | Tailscale only |
| mail-notifier | I built it: counts important Outlook and Gmail emails for Home Base | Home + Tailscale |
| Vaultwarden | My password manager | Tailscale only |
| Uptime Kuma | Watches every app and tells me when something goes down | Home + Tailscale |
| Grafana | Private charts for the flight tracker | Home + Tailscale |
| Pi-hole | Ad blocking for the whole network (DNS) | Admin page: Tailscale only |
| Portainer | Web UI to manage the containers | Tailscale only |
| Caddy | Reverse proxy and HTTPS for the public sites | Ports 80/443 |
| Juanitor | I built it: a local AI that watches the server and tells my phone what broke, see [juanitor](https://github.com/PAV-JUAN/juanitor) | Nobody, it has no web page |
| Ollama | Runs the small AI model Juanitor uses, on the CPU | Server only |
| ntfy | Push notifications to my phone | Public, login required (deny-all by default) |
| Picard | Fixing music tags by hand | Tailscale only |

`apps/INVENTORY.md` is regenerated on every collect and lists exactly what's running.

## How things get in

There are only two ways in.

1. **Public sites** (Jellyfin, Navidrome, Fare Watch and ntfy): the router forwards ports 80 and 443 to the server, Caddy takes it from there and handles HTTPS. CrowdSec reads Caddy's logs and blocks IPs that look like they're probing or attacking, and ufw blocks every port I didn't open on purpose.
2. **Everything else** goes through Tailscale. If your device isn't on my Tailscale network, you can't even see those apps.

The domain's DNS is on Cloudflare, with a small container that updates my home IP whenever my internet provider changes it.

## Decisions I made, and why

Anything that can change settings or holds passwords (Portainer, Vaultwarden, Pi-hole's admin page, qBittorrent) is Tailscale-only. I'd rather connect to Tailscale first than have a login page sitting on the internet.

The download client doesn't have a network of its own. It borrows Gluetun's VPN connection (`network_mode: service:gluetun`), so if the VPN drops, it loses internet instead of leaking my home IP. It's the only app on the VPN; everything else uses the normal connection. It also waits for the VPN container to report healthy before it starts.

Passwords and API keys never go in the compose files. Each app reads them from its own `.env`, which git ignores. This repo only has `.env.example` files with the variable names. `collect.sh` also scans for anything that looks like a password in plain text and stops if it finds one, so I can't commit one by accident.

The website for the flight tracker connects to the database with a user that can only read two tables. Even if there's a bug in my code, it can't change or delete anything.

SSH only accepts keys, no passwords, and never root. My key lives on my PC with a passphrase, and a backup copy is in Vaultwarden.

Security updates install themselves every day. If one needs a reboot, the server restarts at 4 a.m. Montreal time, after the backup. The settings are in `security/updates/`.

Juanitor watches the server, but it can't tell me if the whole server is off. So after every run it pings healthchecks.io, and if the pings stop for 30 minutes, I get an email. That also covers the house losing internet, or Juanitor itself crashing.


## Backups

Every morning at 7:30, restic backs up the apps' data and configs to two places: a 120 GB external SSD plugged into the server, and an encrypted copy on Google Drive (through rclone). The old 4 GB USB stick is now an offline spare in a drawer. Movies and shows are skipped, since those can be downloaded again.

After the backup, a restore test actually restores files and checks them: a canary file that must come back exactly the same, the Vaultwarden database, and the flight tracker's database. The result is written as a status line, and Juanitor alerts my phone if a backup is missed or fails. The scripts are in `backup/`.

The backup log rotates once a week (8 weeks kept), so it doesn't grow forever.

## Things that went wrong (so far)


**My backups never ran.** The restore test compared the backed-up file to the current one, so it failed as soon as I edited anything. It also ran before the backup, and `set -e` stopped the whole script there. So from the day I set it up, not a single scheduled backup happened, and nothing told me. Juanitor found it on its first day. Now the backup runs first, the test can't block it, and the test uses a file that never changes.

**Pi-hole lists that allowed instead of blocked.** I added new lists with the "allowlist" button by mistake, so Pi-hole was told to always allow the domains I wanted blocked.

**Cloudflare's orange cloud.** A new subdomain was proxied through Cloudflare by default, so Caddy couldn't get its HTTPS certificate. My other subdomains are "DNS only" for a reason.

**Free RAM that wasn't.** I tried some spare RAM sticks I had lying around. The PC beeped twice and wouldn't start. They weren't compatible, so the server stays at 10 GB.

## Updating this repo

```bash
./collect.sh     # copy the current setup from the server, write .env.example files, scan for secrets
git diff         # check what changed before committing
git add .
git commit -m "what changed"
git push
```

## Rebuilding the server from this repo

1. Install Ubuntu Server, Docker and Docker Compose.
2. Clone this repo. For each app, copy `.env.example` to `.env` and fill in the values.
3. Restore the app data from the restic backup.
4. In each `apps/<app>/` folder, run `docker compose up -d`.


