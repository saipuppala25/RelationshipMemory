# Memories of Us: Misio i Misia 💕

A private Python/Flask web app — dynamic photo & video collage with drag-and-drop uploads, iPhone QR upload, and optional Cloudflare R2 cloud storage.

---

## 📁 Project Structure

```
memories-of-us/
├── app.py               ← Flask app (dual storage: local disk or Cloudflare R2)
├── requirements.txt     ← Python dependencies
├── templates/
│   ├── login.html
│   ├── gallery.html
│   ├── mobile_upload.html
│   └── mobile_expired.html
├── static/
│   ├── styles.css
│   ├── script.js
│   └── media/           ← Local storage fallback (used when R2 is not configured)
└── README.md
```

---

## 🚀 Running Locally (no cloud setup needed)

```bash
python -m pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** — password: `misioimisia`

Files are saved to `static/media/` on your local disk.

---

## ☁️ Setting Up Cloudflare R2 (free cloud storage)

R2 gives you **10 GB free storage** with **no egress fees** (serving files is free).

### Step 1 — Create a Cloudflare account

Go to [cloudflare.com](https://cloudflare.com) and sign up for free.

### Step 2 — Create an R2 bucket

1. In the Cloudflare dashboard, click **R2 Object Storage** in the left sidebar.
2. Click **Create bucket**.
3. Name it something like `memories-of-us` (lowercase, no spaces).
4. Leave the region as default. Click **Create bucket**.

### Step 3 — Create an API token

1. Still in the R2 section, click **Manage R2 API Tokens** (top right).
2. Click **Create API Token**.
3. Give it a name (e.g. `memories-app`).
4. Set permissions to **Object Read & Write**.
5. Under **Specify bucket**, select your bucket.
6. Click **Create API Token**.
7. **Copy both values** — you won't see the Secret Access Key again:
   - **Access Key ID** → `R2_ACCESS_KEY_ID`
   - **Secret Access Key** → `R2_SECRET_ACCESS_KEY`

### Step 4 — Find your Account ID

In the Cloudflare dashboard, click any domain or go to the R2 overview page.
Your **Account ID** is shown in the right sidebar (32-character hex string).

### Step 5 — Set environment variables

**Windows PowerShell:**
```powershell
$env:R2_BUCKET_NAME       = "memories-of-us"
$env:R2_ACCOUNT_ID        = "your-account-id-here"
$env:R2_ACCESS_KEY_ID     = "your-access-key-id"
$env:R2_SECRET_ACCESS_KEY = "your-secret-access-key"
$env:SITE_PASSWORD        = "misioimisia"
python app.py
```

**Windows CMD:**
```cmd
set R2_BUCKET_NAME=memories-of-us
set R2_ACCOUNT_ID=your-account-id-here
set R2_ACCESS_KEY_ID=your-access-key-id
set R2_SECRET_ACCESS_KEY=your-secret-access-key
set SITE_PASSWORD=misioimisia
python app.py
```

The app will print `Storage backend: Cloudflare R2` on startup when R2 is active.

### Step 6 — Deploying online with R2

When you deploy to Railway, Render, or any host, set the same four env vars
in the platform's environment settings panel. Files will persist permanently
in R2 regardless of redeploys.

---

## 🔑 Changing the Password

Change the `SITE_PASSWORD` environment variable, or edit this line in `app.py`:

```python
CORRECT_PASSWORD = os.environ.get("SITE_PASSWORD", "misioimisia")
```

---

## 📱 iPhone QR Upload

1. Run the app and make sure your phone is on the **same Wi-Fi**.
2. Click **📷 QR Upload** in the navbar.
3. Scan the QR code with your iPhone camera.
4. Upload photos, videos, or a whole album directly from your phone.

---

## 🌐 Deploying to the Web

### Railway (recommended)

1. Push this folder to a GitHub repo.
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub.
3. Set environment variables: `R2_BUCKET_NAME`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `SITE_PASSWORD`, `SECRET_KEY`.
4. Start command: `python app.py`

### Render

1. Push to GitHub.
2. New Web Service → connect repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `python app.py`
5. Add the same environment variables.

---

## 💌 Credits

Made with ♥ for Misia.
