# PSA Queue

Flask app for a PSA-style queue display.

Pages:

- `/` - landing page
- `/controller` - all-queue controller
- `/window/1` to `/window/4` - single-window controllers
- `/display` - public queue display

Public API:

- `GET /api/health`
- `GET /api/state`
- `GET /api/announcements`
- `GET /api/debug-client`

Protected API:

- `POST /api/next/<queue>`
- `POST /api/reset/<queue>`
- `POST /api/max/<queue>`

Protected API requests are allowed from private/LAN IP addresses. For internet deployment, set `QUEUE_ADMIN_TOKEN`. Treat this value as the staff access code for the controller page. Operators enter it once in the **Access code** field, or open the controller once with `?token=YOUR_TOKEN`.

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe app.py
```

Open:

- Controller: `http://127.0.0.1:5000/controller`
- Display: `http://127.0.0.1:5000/display`

## Hostinger Deployment

Hostinger currently supports Flask/Python on VPS plans. Their Flask guidance uses Gunicorn with Nginx on Ubuntu VPS, so this repo includes `wsgi.py`, `gunicorn.conf.py`, and deployment examples in `deploy/`.

On an Ubuntu VPS:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
sudo mkdir -p /var/www/psa-queue
sudo chown -R $USER:$USER /var/www/psa-queue
cd /var/www/psa-queue
git clone YOUR_REPOSITORY_URL .
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Set strong values in `.env`:

```env
FLASK_SECRET_KEY=use-a-long-random-secret
QUEUE_ADMIN_TOKEN=use-a-long-random-controller-token
TRUST_PROXY=true
GUNICORN_BIND=127.0.0.1:8000
```

Install the service:

```bash
sudo cp deploy/psa-queue.service /etc/systemd/system/psa-queue.service
sudo systemctl daemon-reload
sudo systemctl enable --now psa-queue
sudo systemctl status psa-queue
```

Install the Nginx site:

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/psa-queue
sudo nano /etc/nginx/sites-available/psa-queue
sudo ln -s /etc/nginx/sites-available/psa-queue /etc/nginx/sites-enabled/psa-queue
sudo nginx -t
sudo systemctl reload nginx
```

After DNS points to the VPS, add HTTPS with Certbot or Hostinger's preferred SSL setup.

Controller URL:

```text
https://your-domain.com/controller?token=YOUR_QUEUE_ADMIN_TOKEN
```

For regular staff, share this as:

1. Open `https://your-domain.com/controller`
2. Enter the staff access code
3. Click **Unlock**
4. Use **Next**, **Reset**, and **Set max**

Display URL:

```text
https://your-domain.com/display
```

## Docker

```bash
docker build -t psa-queue .
docker run --env-file .env -p 8000:8000 psa-queue
```

## Notes

- Queue and announcement data is stored in memory. Restarting the process resets the queue.
- Do not expose Gunicorn directly to the internet. Put Nginx in front of it or use Docker behind a reverse proxy.
- If `TRUST_PROXY=true`, only run the app behind a trusted reverse proxy that sets `X-Forwarded-For` and `X-Forwarded-Proto`.
