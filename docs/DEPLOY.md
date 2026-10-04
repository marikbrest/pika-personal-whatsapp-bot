# Deploying on Linux

Three ways to run Pika on a Linux server, from simplest to most manual. All of them need the same `.env`
values (see the main README) and one public HTTPS URL for the WhatsApp webhook and Google sign-in.

| Option | Needs | Public HTTPS via |
| --- | --- | --- |
| A. Docker + Cloudflare Tunnel | Docker | Tunnel (no open ports) |
| B. Docker + Caddy | Docker, a domain, ports 80/443 open | Caddy (automatic Let's Encrypt) |
| C. systemd + Caddy, no Docker | Python 3.12+, a domain, ports 80/443 | Caddy |

Tested on Ubuntu 24.04 (ARM64), Python 3.12. Debian 12 and other systemd distributions should work the same.

## A. Docker + Cloudflare Tunnel

Described in the main README ("Run with Docker"): `docker compose --profile tunnel up -d`.

## B. Docker + Caddy

```bash
git clone https://github.com/marikbrest/pika-personal-whatsapp-bot.git && cd pika-personal-whatsapp-bot
cp .env.example .env            # fill it in, and add: PIKA_DOMAIN=assistant.example.com
docker compose -f docker-compose.yml -f docker-compose.caddy.yml up -d --build
docker compose run --rm bot python scripts/create_admin.py 972501234567 "Your Name"
```

Point the DNS record of `PIKA_DOMAIN` at the machine first. Caddy gets the certificate on the first request.
The bot itself stays bound to `127.0.0.1:8000`; only Caddy is public.

## C. systemd, no Docker

```bash
sudo apt update && sudo apt install -y python3-venv git
sudo useradd --system --home /opt/pika --shell /usr/sbin/nologin pika
sudo git clone https://github.com/marikbrest/pika-personal-whatsapp-bot.git /opt/pika
sudo python3 -m venv /opt/pika/venv
sudo /opt/pika/venv/bin/pip install -r /opt/pika/requirements.txt
sudo chown -R pika:pika /opt/pika

sudo install -d -m 750 -o root -g pika /etc/pika
sudo cp /opt/pika/.env.example /etc/pika/pika.env      # edit it; secrets live here, not in /opt/pika
sudo chown root:pika /etc/pika/pika.env && sudo chmod 640 /etc/pika/pika.env

sudo cp /opt/pika/deploy/pika.service /etc/systemd/system/pika.service
sudo systemctl daemon-reload
sudo systemctl enable --now pika
systemctl status pika --no-pager
curl -s http://127.0.0.1:8000/          # {"status":"ok",...}
```

Create the first admin (uses the same environment as the service):

```bash
sudo -u pika bash -c 'set -a; . /etc/pika/pika.env; DB_PATH=/var/lib/pika/assistant.db \
  /opt/pika/venv/bin/python /opt/pika/scripts/create_admin.py 972501234567 "Your Name"'
```

Public HTTPS with Caddy:

```bash
sudo apt install -y caddy
sudo cp /opt/pika/deploy/Caddyfile /etc/caddy/Caddyfile   # edit the hostname first
sudo systemctl reload caddy
```

### Operating it

| Task | Command |
| --- | --- |
| Logs | `journalctl -u pika -f` |
| Restart after editing `/etc/pika/pika.env` | `sudo systemctl restart pika` |
| Update | `sudo -u pika git -C /opt/pika pull && sudo /opt/pika/venv/bin/pip install -r /opt/pika/requirements.txt && sudo systemctl restart pika` |
| Backup | `sudo -u pika env DB_PATH=/var/lib/pika/assistant.db /opt/pika/venv/bin/python /opt/pika/scripts/backup_db.py --out /var/lib/pika/backups` |

The unit file restarts the bot on failure, runs it as an unprivileged user, and makes everything except
`/var/lib/pika` read-only to it. The bot refuses to start twice (single-instance lock), so a stuck old process
shows up as `another instance already holds the single-instance lock`; `systemctl restart pika` clears it.
