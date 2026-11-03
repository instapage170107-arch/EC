# 📦 PackSort OS — Setup Guide

> Forward this to anyone who needs to run the project on their laptop.

---

## 🗂️ Files in the Repo

```
📁 packaging-defect-sorter/
├── main.py                  ← Backend (FastAPI + Camera + Detection + Arduino)
├── requirements.txt         ← Python packages
├── sorter.ino               ← Arduino sketch (upload via Arduino IDE)
├── vercel.json              ← Vercel deployment config
├── .gitignore               ← Ignore rules
├── README.md                ← Full documentation
├── api/
│   └── index.py             ← Vercel serverless entry point
├── templates/
│   ├── index.html           ← Main dashboard UI
│   ├── login.html           ← Admin login page
│   └── admin_logs.html      ← Admin event log page
└── static/
    └── .gitkeep             ← Keeps folder in git
```

> [!IMPORTANT]
> **DO NOT** upload `.venv/`, `myenv/`, `__pycache__/`, `events.db`, or `.vscode/` — they are auto-generated.

---

## 🚀 Setup Steps (Windows)

### Step 1: Clone the Repo
```powershell
git clone https://github.com/YOUR_USERNAME/packaging-defect-sorter.git
cd packaging-defect-sorter
```

### Step 2: Create Virtual Environment
```powershell
python -m venv .venv
.venv\Scripts\activate
```

> [!NOTE]
> You need **Python 3.10 or higher** installed. Check with `python --version`.

### Step 3: Install Dependencies
```powershell
pip install -r requirements.txt
```

This installs: FastAPI, Uvicorn, OpenCV, NumPy, PySerial, Jinja2, python-multipart.

### Step 4: Run the Server
```powershell
uvicorn main:app --host 127.0.0.1 --port 8000
```

### Step 5: Open in Browser
```
http://127.0.0.1:8000
```

✅ You should see the **PackSort OS** dashboard with live camera feed.

---

## 🔌 Arduino Setup (Optional)

If you have an Arduino + Servo motor connected:

### Wiring
```
Arduino Pin 9  ──── Servo Signal (orange/yellow wire)
External 5V    ──── Servo Power  (red wire)
External GND   ──┬─ Servo Ground (brown/black wire)
                 └─ Arduino GND  (must share ground!)
```

### Upload Code
1. Open `sorter.ino` in **Arduino IDE**
2. Select **Tools → Board → Arduino Uno**
3. Select **Tools → Port → COM3** (your port)
4. Click **Upload** ▶
5. **Close Serial Monitor** before starting the Python server

### Connect in Dashboard
1. In the dashboard, find **COM Interface Port**
2. Type your port (e.g., `COM3`)
3. Click **Connect**
4. Green status = Connected ✔

---

## 🔧 Setup on Mac / Linux

```bash
git clone https://github.com/YOUR_USERNAME/packaging-defect-sorter.git
cd packaging-defect-sorter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

> [!NOTE]
> On Mac, Arduino port will be `/dev/tty.usbmodem14101` or similar (check `ls /dev/tty.*`).
> On Linux, it'll be `/dev/ttyACM0` or `/dev/ttyUSB0`.

---

## ☁️ Vercel Deployment (Online Demo)

To get a **public URL** anyone can access (runs in simulation mode — no camera):

```powershell
npm i -g vercel
vercel login
vercel --prod
```

The Vercel URL will show an animated conveyor belt simulation with full UI.

---

## 🎯 How It Works

1. **Camera** captures frames from your laptop webcam
2. A **vertical green line** (tripwire) sits at the center of the feed
3. When a package crosses the line, the system checks if it's **torn/damaged**
4. Detection uses **contour analysis** — torn packages have irregular edges (low solidity)
5. If torn → event logged, signal sent to Arduino → **servo arm blocks the package**
6. If good → package passes through normally

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| **Black screen / no camera** | Click "Reconnect Cam" button. Check Windows Privacy Settings → Camera → allow desktop apps |
| **Arduino won't connect** | Close Arduino Serial Monitor first. Check COM port number in Device Manager |
| **pip install fails** | Make sure `.venv` is activated (you should see `(.venv)` in terminal) |
| **Port 8000 in use** | Use `--port 8001` instead |
| **OpenCV error on install** | Try `pip install opencv-python-headless>=4.8` instead |

---

## 📱 Admin Panel

- URL: `http://127.0.0.1:8000/admin`
- Username: `admin`
- Password: `admin`

---

## 🔑 Environment Variables (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_PORT` | — | Arduino port (e.g., `COM3`) |
| `ADMIN_USER` | `admin` | Admin login username |
| `ADMIN_PASS` | `admin` | Admin login password |

---

**That's it! 🚀 Server should be running at `http://127.0.0.1:8000`**
