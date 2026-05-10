## Credits

This project is based on [MasterHttpRelayVPN](https://github.com/masterking32/MasterHttpRelayVPN).

<p align="center">
  <img
    src="https://github.com/user-attachments/assets/5ac60b07-2c70-492d-8aa0-399dd7213a08"
    alt="MHR Dashboard"
    width="100%"
  />
</p>

# MHR Exit Node Server Setup

<p dir="rtl" align="right">
این راهنما مربوط به راه‌اندازی سمت سرور <code>MHR Exit Node</code> است.
</p>

<p dir="rtl" align="right">
در این روش ابتدا یک پوشه به نام <code>MHRVPS</code> روی سرور ساخته می‌شود، سپس فایل سرور داخل آن قرار می‌گیرد و با <code>PM2</code> اجرا می‌شود.
</p>

<p dir="rtl" align="right">
فایل سرور:
</p>

```text
server.js
```

<p dir="rtl" align="right">
مسیر نهایی فایل روی سرور:
</p>

```text
/root/MHRVPS/server.js
```

<p dir="rtl" align="right">
این فایل باید روی VPS یا سرور لینوکسی اجرا شود و وظیفه‌ی آن دریافت درخواست از Google Apps Script و رله کردن ترافیک به مقصد نهایی است.
</p>

---

## 1. پیش‌نیازها

<p dir="rtl" align="right">
روی سرور باید <code>Node.js</code> و <code>npm</code> نصب باشد.
</p>

<p dir="rtl" align="right">
برای Ubuntu / Debian:
</p>

```bash
apt update
apt install -y nodejs npm
```

<p dir="rtl" align="right">
بررسی نسخه‌ها:
</p>

```bash
node -v
npm -v
```

<p dir="rtl" align="right">
پیشنهاد می‌شود از Node.js نسخه <code>18</code> یا <code>20</code> استفاده شود.
</p>

---

## 2. ساخت پوشه سرور

<p dir="rtl" align="right">
ابتدا پوشه اجرای سرور را بسازید:
</p>

```bash
mkdir -p /root/MHRVPS
```

<p dir="rtl" align="right">
وارد پوشه شوید:
</p>

```bash
cd /root/MHRVPS
```

<p dir="rtl" align="right">
حالا فایل <code>server.js</code> را داخل همین مسیر قرار دهید:
</p>

```text
/root/MHRVPS/server.js
```

<p dir="rtl" align="right">
در پایان این مرحله، مسیر فایل باید این باشد:
</p>

```text
/root/MHRVPS/server.js
```

---

## 3. نصب PM2

<p dir="rtl" align="right">
برای اجرای دائمی <code>server.js</code> از <code>PM2</code> استفاده می‌شود.
</p>

```bash
npm install -g pm2
```

<p dir="rtl" align="right">
بررسی نصب:
</p>

```bash
pm2 -v
```

---

## 4. متغیرهای قابل تنظیم

<p dir="rtl" align="right">
فایل <code>server.js</code> مقدارهای اصلی را از Environment Variable می‌خواند.
</p>

| Variable | Required | Description |
|---|---:|---|
| `EXIT_NODE_PSK` | Yes | Main key between Apps Script and Exit Node |
| `HEALTH_KEY` | Optional | Health endpoint key |
| `PORT` | Optional | Server port, default `8081` |
| `HOST` | Optional | Listen host, default `0.0.0.0` |
| `MAX_INFLIGHT` | Optional | Max concurrent requests |
| `MAX_SOCKETS` | Optional | Max sockets |
| `MAX_FREE_SOCKETS` | Optional | Max free sockets |
| `MAX_REQUEST_BODY` | Optional | Max request body size |
| `MAX_RESPONSE_BODY` | Optional | Max response body size |
| `MEMORY_SOFT_LIMIT_MB` | Optional | Soft RAM limit |
| `MEMORY_HARD_LIMIT_MB` | Optional | Hard RAM limit |

<p dir="rtl" align="right">
مقدار <code>EXIT_NODE_PSK</code> باید بعداً با مقدار <code>AUTH_KEY</code> در Google Apps Script و مقدار <code>auth_key</code> در کلاینت یکی باشد.
</p>

<p dir="rtl" align="right">
مقدار <code>HEALTH_KEY</code> برای بررسی وضعیت Health سرور استفاده می‌شود و بعداً در Setup Wizard کلاینت وارد می‌شود.
</p>

---

## 5. اجرای تستی بدون PM2

<p dir="rtl" align="right">
ابتدا می‌توانید سرور را به صورت دستی اجرا کنید.
</p>

```bash
cd /root/MHRVPS

EXIT_NODE_PSK="CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY" \
HEALTH_KEY="CHANGE_ME_TO_YOUR_HEALTH_KEY" \
PORT=8081 \
HOST="0.0.0.0" \
node server.js
```

<p dir="rtl" align="right">
اگر اجرا موفق باشد، خروجی شبیه این نمایش داده می‌شود:
</p>

```text
Secure optimized exit node running on 0.0.0.0:8081
MAX_REQUEST_BODY=33554432
MAX_RESPONSE_BODY=50331648
MAX_INFLIGHT=48
MAX_SOCKETS=64
TIMEOUT_FAST_MS=45000
TIMEOUT_SLOW_MS=90000
TIMEOUT_VIDEO_MS=90000
MEMORY_GUARD_ENABLED=true
MEMORY_SOFT_LIMIT_MB=420
MEMORY_HARD_LIMIT_MB=620
HEALTH_AUTH=enabled
```

<p dir="rtl" align="right">
برای توقف اجرای دستی:
</p>

```text
CTRL + C
```

---

## 6. اجرای سرور با PM2

<p dir="rtl" align="right">
داخل مسیر <code>/root/MHRVPS</code> دستور زیر را اجرا کنید:
</p>

```bash
cd /root/MHRVPS

EXIT_NODE_PSK="CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY" \
HEALTH_KEY="CHANGE_ME_TO_YOUR_HEALTH_KEY" \
PORT=8081 \
HOST="0.0.0.0" \
pm2 start server.js --name mhr-exit-node --update-env
```

<p dir="rtl" align="right">
بررسی وضعیت:
</p>

```bash
pm2 status
```

<p dir="rtl" align="right">
دیدن لاگ‌ها:
</p>

```bash
pm2 logs mhr-exit-node
```

---

## 7. اجرای خودکار بعد از ری‌استارت سرور

<p dir="rtl" align="right">
بعد از اجرای موفق سرویس، وضعیت PM2 را ذخیره کنید:
</p>

```bash
pm2 save
```

<p dir="rtl" align="right">
سپس دستور Startup را اجرا کنید:
</p>

```bash
pm2 startup
```

<p dir="rtl" align="right">
بعد از اجرای <code>pm2 startup</code>، یک دستور توسط PM2 نمایش داده می‌شود. همان دستور را کپی و اجرا کنید.
</p>

<p dir="rtl" align="right">
در پایان دوباره بزنید:
</p>

```bash
pm2 save
```

---

## 8. تست Health از داخل سرور

<p dir="rtl" align="right">
روی خود سرور این دستور را اجرا کنید:
</p>

```bash
curl http://127.0.0.1:8081/health
```

<p dir="rtl" align="right">
خروجی نمونه:
</p>

```json
{
  "ok": true,
  "status": "healthy",
  "inflight": 0,
  "totalRequests": 0,
  "totalErrors": 0,
  "totalBytesIn": 0,
  "totalBytesOut": 0
}
```

---

## 9. تست Health از بیرون سرور

<p dir="rtl" align="right">
با query string:
</p>

```bash
curl "http://YOUR_VPS_IP:8081/health?k=CHANGE_ME_TO_YOUR_HEALTH_KEY"
```

<p dir="rtl" align="right">
یا با header:
</p>

```bash
curl -H "x-health-key: CHANGE_ME_TO_YOUR_HEALTH_KEY" http://YOUR_VPS_IP:8081/health
```

<p dir="rtl" align="right">
اگر مقدار Health Key درست نباشد:
</p>

```json
{"e":"forbidden"}
```

---

## 10. باز کردن پورت فایروال

<p dir="rtl" align="right">
اگر <code>ufw</code> فعال است:
</p>

```bash
ufw allow 8081/tcp
ufw reload
ufw status
```

<p dir="rtl" align="right">
اگر سرور پشت Firewall پنل دیتاسنتر یا Provider است، پورت <code>8081/tcp</code> را از همان پنل هم باز کنید.
</p>

---

## 11. اتصال Google Apps Script به Exit Node

<p dir="rtl" align="right">
در فایل Google Apps Script مقدارها باید مطابق سرور تنظیم شوند.
</p>

<p dir="rtl" align="right">
بخش ابتدایی فایل Google Apps Script به شکل زیر است:
</p>

```js
const AUTH_KEY = "CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY";
const VPS_IP = "YOUR_VPS_IP_HERE";
const WORKER_URL = "http://" + VPS_IP + ":8081";
```

<p dir="rtl" align="right">
مقدار <code>AUTH_KEY</code> در Google Apps Script باید با مقدار <code>EXIT_NODE_PSK</code> روی سرور یکی باشد.
</p>

<p dir="rtl" align="right">
مثال روی سرور:
</p>

```bash
EXIT_NODE_PSK="MHR-Private-Relay-Key-2026"
```

<p dir="rtl" align="right">
مثال داخل Google Apps Script:
</p>

```js
const AUTH_KEY = "MHR-Private-Relay-Key-2026";
```

<p dir="rtl" align="right">
مقدار <code>VPS_IP</code> هم باید برابر IP سرور باشد.
</p>

```js
const VPS_IP = "YOUR_VPS_IP_HERE";
```

<p dir="rtl" align="right">
خط <code>WORKER_URL</code> نیاز به تغییر ندارد و به صورت خودکار از روی <code>VPS_IP</code> ساخته می‌شود.
</p>

```js
const WORKER_URL = "http://" + VPS_IP + ":8081";
```

---

## 12. مقدارهایی که باید نگه دارید

<p dir="rtl" align="right">
بعد از راه‌اندازی سمت سرور، این مقدارها برای مراحل بعد لازم هستند:
</p>

| Value | Used in |
|---|---|
| `EXIT_NODE_PSK` | Google Apps Script `AUTH_KEY` and Client `auth_key` |
| `HEALTH_KEY` | Client `exit_node_health_key` |
| `VPS IP` | Google Apps Script `VPS_IP` and Client Health URL |
| `Health URL` | Client `exit_node_health_url` |

<p dir="rtl" align="right">
نمونه Health URL:
</p>

```text
http://YOUR_VPS_IP:8081
```

---

## 13. دستورات کاربردی PM2

<p dir="rtl" align="right">
وضعیت سرویس:
</p>

```bash
pm2 status
```

<p dir="rtl" align="right">
لاگ‌ها:
</p>

```bash
pm2 logs mhr-exit-node
```

<p dir="rtl" align="right">
ری‌استارت:
</p>

```bash
pm2 restart mhr-exit-node --update-env
```

<p dir="rtl" align="right">
توقف:
</p>

```bash
pm2 stop mhr-exit-node
```

<p dir="rtl" align="right">
حذف از PM2:
</p>

```bash
pm2 delete mhr-exit-node
```

<p dir="rtl" align="right">
ذخیره وضعیت فعلی:
</p>

```bash
pm2 save
```

---

## 14. تغییر مقدارهای اجرا

<p dir="rtl" align="right">
برای تغییر <code>EXIT_NODE_PSK</code>، <code>HEALTH_KEY</code>، پورت یا سایر مقدارها:
</p>

```bash
cd /root/MHRVPS

EXIT_NODE_PSK="NEW_PRIVATE_AUTH_KEY" \
HEALTH_KEY="NEW_HEALTH_KEY" \
PORT=8081 \
HOST="0.0.0.0" \
pm2 restart mhr-exit-node --update-env
```

<p dir="rtl" align="right">
بعد از تغییر:
</p>

```bash
pm2 save
```

<p dir="rtl" align="right">
اگر مقدار <code>EXIT_NODE_PSK</code> تغییر کند، مقدار <code>AUTH_KEY</code> در Google Apps Script و مقدار <code>auth_key</code> در کلاینت هم باید با مقدار جدید هماهنگ شود.
</p>

<p dir="rtl" align="right">
اگر مقدار <code>HEALTH_KEY</code> تغییر کند، مقدار <code>exit_node_health_key</code> در کلاینت هم باید با مقدار جدید هماهنگ شود.
</p>

---

## 15. نمونه اجرای کامل

```bash
mkdir -p /root/MHRVPS
cd /root/MHRVPS

# Put server.js in this folder before running PM2.

EXIT_NODE_PSK="CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY" \
HEALTH_KEY="CHANGE_ME_TO_YOUR_HEALTH_KEY" \
PORT=8081 \
HOST="0.0.0.0" \
pm2 start server.js --name mhr-exit-node --update-env

pm2 save
pm2 startup
pm2 save
```

---

## 16. بررسی نهایی

<p dir="rtl" align="right">
بررسی وضعیت PM2:
</p>

```bash
pm2 status
```

<p dir="rtl" align="right">
تست Health از داخل سرور:
</p>

```bash
curl http://127.0.0.1:8081/health
```

<p dir="rtl" align="right">
تست Health از بیرون سرور:
</p>

```bash
curl "http://YOUR_VPS_IP:8081/health?k=CHANGE_ME_TO_YOUR_HEALTH_KEY"
```

<p dir="rtl" align="right">
اگر PM2 وضعیت <code>online</code> نشان دهد و Health پاسخ <code>ok: true</code> بدهد، Exit Node آماده استفاده است.
</p>

# MHR Google Apps Script Relay Setup

<p dir="rtl" align="right">
این راهنما مربوط به راه‌اندازی رله <code>Google Apps Script</code> برای پروژه <code>MHR</code> است.
</p>

<p dir="rtl" align="right">
فایل Google Apps Script در مسیر زیر قرار دارد:
</p>

```text
MHR-VPS/script/Code.gs
```

<p dir="rtl" align="right">
این فایل باید داخل Google Apps Script قرار بگیرد و وظیفه‌ی آن ارسال درخواست‌های کلاینت MHR به Exit Node سرور است.
</p>

---

## 1. مقدارهای مورد نیاز

<p dir="rtl" align="right">
قبل از شروع این بخش، باید سرور Exit Node اجرا شده باشد.
</p>

<p dir="rtl" align="right">
برای راه‌اندازی Google Apps Script به این مقدارها نیاز دارید:
</p>

| Value | Description |
|---|---|
| `VPS IP` | IP سروری که `server.js` روی آن اجرا شده است |
| `EXIT_NODE_PSK` | همان کلید اصلی که روی سرور تنظیم شده است |
| `PORT` | پورت اجرای Exit Node، پیش‌فرض `8081` |

<p dir="rtl" align="right">
در فایل Google Apps Script فقط مقدارهای <code>AUTH_KEY</code> و <code>VPS_IP</code> را تنظیم می‌کنید.
</p>

---

## 2. ساخت پروژه در Google Apps Script

<p dir="rtl" align="right">
وارد Google Apps Script شوید:
</p>

```text
https://script.google.com
```

<p dir="rtl" align="right">
روی <code>New project</code> کلیک کنید.
</p>

<p dir="rtl" align="right">
بعد از باز شدن پروژه، محتوای پیش‌فرض فایل <code>Code.gs</code> را کامل پاک کنید.
</p>

---

## 3. قرار دادن کد پروژه

<p dir="rtl" align="right">
در پروژه MHR این فایل را باز کنید:
</p>

```text
MHR-VPS/script/Code.gs
```

<p dir="rtl" align="right">
تمام کد داخل فایل را کپی کنید و داخل فایل <code>Code.gs</code> در Google Apps Script قرار دهید.
</p>

---

## 4. تنظیم AUTH_KEY

<p dir="rtl" align="right">
داخل کد این خط را پیدا کنید:
</p>

```js
const AUTH_KEY = "CHANGE_ME_TO_YOUR_PRIVATE_AUTH_KEY";
```

<p dir="rtl" align="right">
مقدار داخل کوتیشن باید دقیقاً با مقدار <code>EXIT_NODE_PSK</code> سمت سرور یکی باشد.
</p>

<p dir="rtl" align="right">
مثال، اگر سرور را این‌طور اجرا کرده‌اید:
</p>

```bash
EXIT_NODE_PSK="MHR-Private-Relay-Key-2026"
```

<p dir="rtl" align="right">
داخل Google Apps Script باید این‌طور باشد:
</p>

```js
const AUTH_KEY = "MHR-Private-Relay-Key-2026";
```

---

## 5. تنظیم IP سرور

<p dir="rtl" align="right">
داخل کد این خط را پیدا کنید:
</p>

```js
const VPS_IP = "YOUR_VPS_IP_HERE";
```

<p dir="rtl" align="right">
به جای <code>YOUR_VPS_IP_HERE</code> آی‌پی سرور خودتان را قرار دهید.
</p>

<p dir="rtl" align="right">
مثال:
</p>

```js
const VPS_IP = "YOUR_SERVER_IP";
```

<p dir="rtl" align="right">
این خط نیاز به تغییر ندارد:
</p>

```js
const WORKER_URL = "http://" + VPS_IP + ":8081";
```

<p dir="rtl" align="right">
مقدار <code>WORKER_URL</code> به صورت خودکار از روی <code>VPS_IP</code> ساخته می‌شود.
</p>

---

## 6. Deploy کردن Web App

<p dir="rtl" align="right">
از منوی بالای Google Apps Script وارد مسیر زیر شوید:
</p>

```text
Deploy -> New deployment
```

<p dir="rtl" align="right">
نوع Deployment را روی <code>Web app</code> قرار دهید.
</p>

<p dir="rtl" align="right">
تنظیمات را این‌طور انتخاب کنید:
</p>

| Option | Value |
|---|---|
| `Execute as` | `Me` |
| `Who has access` | `Anyone` |

<p dir="rtl" align="right">
سپس روی <code>Deploy</code> کلیک کنید.
</p>

<p dir="rtl" align="right">
اگر صفحه دسترسی‌ها نمایش داده شد، دسترسی‌ها را تأیید کنید.
</p>

---

## 7. کپی کردن Deployment ID

<p dir="rtl" align="right">
بعد از Deploy، مقدار <code>Deployment ID</code> را کپی کنید.
</p>

<p dir="rtl" align="right">
این مقدار معمولاً شبیه این است:
</p>

```text
AKfycbxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

<p dir="rtl" align="right">
این مقدار برای مرحله نصب کلاینت لازم است.
</p>

---

## 8. مقدارهایی که باید برای کلاینت نگه دارید

<p dir="rtl" align="right">
بعد از راه‌اندازی Google Apps Script، این مقدارها برای Setup Wizard کلاینت لازم هستند:
</p>

| Value | Used in Client |
|---|---|
| `Deployment ID` | داخل فیلد Deployment ID |
| `auth_key` | همان مقدار `EXIT_NODE_PSK` / `AUTH_KEY` |
| `Exit Node Health URL` | آدرس Health سرور |
| `Exit Node Health Key` | همان مقدار `HEALTH_KEY` سمت سرور |

<p dir="rtl" align="right">
نمونه:
</p>

```text
Deployment ID: AKfycbxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
auth_key: MHR-Private-Relay-Key-2026
Exit Node Health URL: http://YOUR_VPS_IP:8081
Exit Node Health Key: CHANGE_ME_TO_YOUR_HEALTH_KEY
```

---

## 9. هماهنگی مقدارها

<p dir="rtl" align="right">
این مقدارها باید با هم یکی باشند:
</p>

<p dir="rtl" align="right">
روی سرور:
</p>

```bash
EXIT_NODE_PSK="MHR-Private-Relay-Key-2026"
```

<p dir="rtl" align="right">
داخل Google Apps Script:
</p>

```js
const AUTH_KEY = "MHR-Private-Relay-Key-2026";
```

<p dir="rtl" align="right">
داخل Setup Wizard کلاینت:
</p>

```text
auth_key: MHR-Private-Relay-Key-2026
```

<p dir="rtl" align="right">
اگر این مقدارها یکی نباشند، اتصال رله کار نمی‌کند.
</p>

---

## 10. نمونه تنظیم نهایی Code.gs

<p dir="rtl" align="right">
نمونه بخش ابتدایی فایل <code>Code.gs</code> بعد از تنظیم:
</p>

```js
const AUTH_KEY = "MHR-Private-Relay-Key-2026";
const VPS_IP = "YOUR_VPS_IP_HERE";
const WORKER_URL = "http://" + VPS_IP + ":8081";
```

<p dir="rtl" align="right">
فقط مقدارهای <code>AUTH_KEY</code> و <code>VPS_IP</code> را تغییر دهید.
</p>

---

# MHR Client Setup

<p dir="rtl" align="right">
این راهنما مربوط به راه‌اندازی سمت کلاینت <code>MHR</code> است.
</p>

<p dir="rtl" align="right">
کلاینت روی سیستم کاربر اجرا می‌شود، فایل <code>config.json</code> را می‌سازد، Dashboard را بالا می‌آورد و از طریق Google Apps Script به Exit Node متصل می‌شود.
</p>

---

# MHR Client Setup

<p dir="rtl" align="right">
این راهنما مربوط به راه‌اندازی سمت کلاینت <code>MHR</code> است.
</p>

<p dir="rtl" align="right">
کلاینت روی سیستم کاربر اجرا می‌شود، فایل <code>config.json</code> را می‌سازد، Dashboard را بالا می‌آورد، و از طریق Google Apps Script به Exit Node متصل می‌شود.
</p>

---

## 1. پیش‌نیازها

<p dir="rtl" align="right">
قبل از راه‌اندازی کلاینت، این مراحل باید انجام شده باشند:
</p>

<p dir="rtl" align="right">
۱. Exit Node روی سرور اجرا شده باشد.
</p>

<p dir="rtl" align="right">
۲. Google Apps Script ساخته و Deploy شده باشد.
</p>

<p dir="rtl" align="right">
۳. مقدارهای مورد نیاز برای Setup Wizard آماده باشند.
</p>

<p dir="rtl" align="right">
۴. Python روی سیستم نصب باشد.
</p>

<p dir="rtl" align="right">
مقدارهای مورد نیاز:
</p>

| Value | Description |
|---|---|
| `Deployment ID` | از Google Apps Script |
| `auth_key` | همان مقدار `AUTH_KEY` داخل Google Apps Script و `EXIT_NODE_PSK` سمت سرور |
| `Exit Node Health URL` | آدرس Health سرور |
| `Exit Node Health Key` | همان مقدار `HEALTH_KEY` سمت سرور |

<p dir="rtl" align="right">
نمونه:
</p>

```text
Deployment ID: AKfycbxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
auth_key: MHR-Private-Relay-Key-2026
Exit Node Health URL: http://YOUR_VPS_IP:8081
Exit Node Health Key: CHANGE_ME_TO_YOUR_HEALTH_KEY
```

---

## 2. نصب پکیج‌های Python

<p dir="rtl" align="right">
قبل از اجرای <code>main.py</code> باید پکیج‌های مورد نیاز از فایل <code>requirements.txt</code> نصب شوند.
</p>

<p dir="rtl" align="right">
وارد پوشه پروژه شوید.
</p>

<p dir="rtl" align="right">
مثلاً اگر پروژه روی دسکتاپ است:
</p>

```text
C:\Users\YOUR_USER\Desktop\MHR-VPS
```

<p dir="rtl" align="right">
داخل همین پوشه CMD باز کنید.
</p>

<p dir="rtl" align="right">
سپس دستور زیر را اجرا کنید:
</p>

```bat
python -m pip install -r requirements.txt
```

<p dir="rtl" align="right">
اگر دستور بالا اجرا نشد، این دستور را امتحان کنید:
</p>

```bat
py -m pip install -r requirements.txt
```

<p dir="rtl" align="right">
بعد از نصب موفق پکیج‌ها، می‌توانید کلاینت را اجرا کنید.
</p>

---

## 3. اجرای کلاینت

<p dir="rtl" align="right">
داخل پوشه پروژه CMD باز کنید.
</p>

<p dir="rtl" align="right">
سپس برنامه را اجرا کنید:
</p>

```bat
main.py
```

<p dir="rtl" align="right">
اگر با دستور بالا اجرا نشد:
</p>

```bat
python main.py
```

<p dir="rtl" align="right">
یا:
</p>

```bat
py main.py
```

<p dir="rtl" align="right">
بعد از اجرا، Setup Wizard در مرورگر باز می‌شود.
</p>

<p dir="rtl" align="right">
اگر خودکار باز نشد، این آدرس را دستی باز کنید:
</p>

```text
http://127.0.0.1:9099/setup
```

---

## 4. نصب Certificate

<p dir="rtl" align="right">
بعد از اجرای اولیه، اگر برنامه برای نصب Certificate راهنما یا فایل نصب Certificate نشان داد، آن را نصب کنید.
</p>

<p dir="rtl" align="right">
Certificate برای عبور درست ترافیک HTTPS از پروکسی محلی MHR استفاده می‌شود.
</p>

<p dir="rtl" align="right">
بعد از نصب Certificate، مرورگر را یک بار کامل ببندید و دوباره باز کنید.
</p>

---

## 5. انتخاب زبان Setup Wizard

<p dir="rtl" align="right">
در صفحه Setup Wizard ابتدا زبان را انتخاب کنید.
</p>

```text
فارسی
```

<p dir="rtl" align="right">
یا:
</p>

```text
English
```

<p dir="rtl" align="right">
زبان انتخاب‌شده برای Dashboard، Downloader و Suggestions هم استفاده می‌شود.
</p>

---

## 6. وارد کردن auth_key

<p dir="rtl" align="right">
در مرحله <code>Apps Script information</code> این فیلد را پر کنید:
</p>

```text
auth_key مشترک
```

<p dir="rtl" align="right">
این مقدار باید با مقدارهای زیر یکی باشد:
</p>

<p dir="rtl" align="right">
روی سرور:
</p>

```bash
EXIT_NODE_PSK="..."
```

<p dir="rtl" align="right">
داخل Google Apps Script:
</p>

```js
const AUTH_KEY = "...";
```

<p dir="rtl" align="right">
داخل Setup Wizard:
</p>

```text
auth_key
```

<p dir="rtl" align="right">
مثال:
</p>

```text
MHR-Private-Relay-Key-2026
```

---

## 7. وارد کردن Deployment ID

<p dir="rtl" align="right">
در بخش <code>Deployment ID ها</code>، مقدار Deployment ID که از Google Apps Script گرفته‌اید را وارد کنید.
</p>

<p dir="rtl" align="right">
هر بار فقط یک Deployment ID وارد کنید و روی دکمه افزودن بزنید.
</p>

<p dir="rtl" align="right">
نمونه:
</p>

```text
AKfycbxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

<p dir="rtl" align="right">
بعد از افزودن، مقدار داخل لیست نمایش داده می‌شود.
</p>

<p dir="rtl" align="right">
قوانین وارد کردن Deployment ID:
</p>

| Rule | Value |
|---|---|
| One ID each time | Yes |
| Space | Not allowed |
| Comma | Not allowed |
| Bracket | Not allowed |
| Quote | Not allowed |
| New line | Not allowed |

<p dir="rtl" align="right">
برای سرعت و پایداری بهتر می‌توانید چند Deployment ID اضافه کنید.
</p>

<p dir="rtl" align="right">
پیشنهاد معمول:
</p>

```text
5 Deployment ID
```

---

## 8. پوشه دانلودها

<p dir="rtl" align="right">
فیلد <code>پوشه دانلودها</code> مسیر ذخیره دانلودها را مشخص می‌کند.
</p>

<p dir="rtl" align="right">
مقدار پیش‌فرض:
</p>

```text
downloads
```

<p dir="rtl" align="right">
یعنی فایل‌ها داخل پوشه <code>downloads</code> در مسیر پروژه ذخیره می‌شوند.
</p>

<p dir="rtl" align="right">
اگر مسیر دلخواه دارید، می‌توانید آن را وارد کنید.
</p>

<p dir="rtl" align="right">
نمونه:
</p>

```text
D:\Downloads\MHR
```

---

## 9. تنظیم Exit Node Health

<p dir="rtl" align="right">
در مرحله <code>Exit Node / Server Health</code> می‌توانید اطلاعات Health سرور را وارد کنید.
</p>

<p dir="rtl" align="right">
این بخش برای نمایش وضعیت سرور در Dashboard استفاده می‌شود.
</p>

<p dir="rtl" align="right">
فیلد اول:
</p>

```text
آدرس Health نود خروجی
```

<p dir="rtl" align="right">
نمونه:
</p>

```text
http://YOUR_VPS_IP:8081
```

<p dir="rtl" align="right">
فیلد دوم:
</p>

```text
exit_node_health_key
```

<p dir="rtl" align="right">
این مقدار همان <code>HEALTH_KEY</code> سمت سرور است.
</p>

<p dir="rtl" align="right">
نمونه:
</p>

```text
CHANGE_ME_TO_YOUR_HEALTH_KEY
```

<p dir="rtl" align="right">
اگر این بخش را پر کنید، Dashboard می‌تواند وضعیت سرور را نشان دهد.
</p>

---

## 10. بررسی نهایی

<p dir="rtl" align="right">
در مرحله آخر، Setup Wizard خلاصه تنظیمات را نمایش می‌دهد.
</p>

| Item | Description |
|---|---|
| Deployment ID Count | تعداد IDهایی که اضافه کرده‌اید |
| Exit Node Health | فعال یا غیرفعال بودن Health |
| Default Language | زبان Dashboard |
| Restart | بعد از ذخیره، برنامه ری‌استارت می‌شود |

<p dir="rtl" align="right">
برای ذخیره تنظیمات روی دکمه زیر بزنید:
</p>

```text
ساخت config.json و راه‌اندازی
```

<p dir="rtl" align="right">
یا در نسخه انگلیسی:
</p>

```text
Create config.json and start
```

---

## 11. ساخته شدن config.json

<p dir="rtl" align="right">
بعد از پایان Setup Wizard، فایل زیر ساخته می‌شود:
</p>

```text
config.json
```

<p dir="rtl" align="right">
این فایل شامل تنظیمات اصلی کلاینت است.
</p>

<p dir="rtl" align="right">
بعد از ذخیره، MHR خودش ری‌استارت می‌شود و Dashboard باز می‌شود.
</p>

<p dir="rtl" align="right">
آدرس Dashboard:
</p>

```text
http://127.0.0.1:9099
```

---

## 12. پورت‌های کلاینت

<p dir="rtl" align="right">
بعد از اجرا، MHR چند سرویس محلی باز می‌کند:
</p>

| Service | Address |
|---|---|
| Dashboard | `http://127.0.0.1:9099` |
| HTTP Proxy | `127.0.0.1:8085` |
| SOCKS5 Proxy | `127.0.0.1:1080` |
| Setup Wizard | `http://127.0.0.1:9099/setup` |

<p dir="rtl" align="right">
برای استفاده در مرورگر یا سیستم، پروکسی را روی یکی از این‌ها تنظیم کنید.
</p>

<p dir="rtl" align="right">
HTTP Proxy:
</p>

```text
127.0.0.1
8085
```

<p dir="rtl" align="right">
SOCKS5 Proxy:
</p>

```text
127.0.0.1
1080
```

---

## 13. بررسی وضعیت در Dashboard

<p dir="rtl" align="right">
بعد از راه‌اندازی، Dashboard را باز کنید:
</p>

```text
http://127.0.0.1:9099
```

<p dir="rtl" align="right">
بخش‌های اصلی Dashboard:
</p>

| Section | Usage |
|---|---|
| Overview | وضعیت کلی سیستم |
| Network | وضعیت پروکسی و مسیر اتصال |
| Modes & Features | کنترل Turbo، H2، SABR و Video Prefetch |
| Video / YouTube | وضعیت تنظیمات ویدیو |
| Downloads | وضعیت دانلودر |
| Scripts | مصرف و وضعیت Apps Script ID ها |
| Script Optimizer | مدیریت Script ID و Tier |
| Diagnostics | وضعیت Exit Node و Health |
| Logs | لاگ‌های زنده |
| Config | تنظیمات Runtime |

---

## 14. Script Optimizer

<p dir="rtl" align="right">
بعد از وارد کردن Deployment ID ها، Setup Wizard تنظیمات را بر اساس تعداد IDها بهینه می‌کند.
</p>

<p dir="rtl" align="right">
Tierها بر اساس تعداد Script ID:
</p>

| Script ID Count | Tier |
|---:|---:|
| 1 | Tier 1 |
| 2 | Tier 2 |
| 3 | Tier 3 |
| 4 | Tier 4 |
| 5 or more | Tier 5 |

<p dir="rtl" align="right">
اگر بعداً ID جدید اضافه کردید، از بخش زیر وارد شوید:
</p>

```text
Dashboard -> Script Optimizer
```

<p dir="rtl" align="right">
در آنجا می‌توانید ID اضافه یا حذف کنید، Duplicateها را پاک کنید و Optimization را Apply کنید.
</p>

---

## 15. تست اتصال

<p dir="rtl" align="right">
بعد از راه‌اندازی، این موارد را بررسی کنید:
</p>

<p dir="rtl" align="right">
۱. Dashboard باز شود:
</p>

```text
http://127.0.0.1:9099
```

<p dir="rtl" align="right">
۲. اگر Health را تنظیم کرده‌اید، وضعیت Exit Node در Dashboard آنلاین شود.
</p>

<p dir="rtl" align="right">
۳. در بخش Scripts حداقل یک Script ID نمایش داده شود.
</p>

<p dir="rtl" align="right">
۴. در بخش Logs خطاهای اتصال تکراری وجود نداشته باشد.
</p>

<p dir="rtl" align="right">
۵. مرورگر با پروکسی محلی باز شود و سایت‌ها لود شوند.
</p>

---

## 16. اجرای دوباره بعد از Setup

<p dir="rtl" align="right">
بعد از اینکه <code>config.json</code> ساخته شد، دفعات بعد فقط کافی است داخل پوشه پروژه CMD باز کنید و اجرا کنید:
</p>

```bat
main.py
```

<p dir="rtl" align="right">
یا:
</p>

```bat
python main.py
```

<p dir="rtl" align="right">
یا:
</p>

```bat
py main.py
```

<p dir="rtl" align="right">
اگر Setup قبلاً کامل شده باشد، برنامه مستقیم Dashboard و سرویس‌های پروکسی را اجرا می‌کند.
</p>

---

## 17. اجرای دوباره Setup Wizard

<p dir="rtl" align="right">
اگر خواستید تنظیمات اولیه را دوباره انجام دهید، آدرس زیر را باز کنید:
</p>

```text
http://127.0.0.1:9099/setup
```

<p dir="rtl" align="right">
در نسخه فعلی، مقدارهای فرم به صورت خودکار پر نمی‌شوند و باید دوباره دستی وارد شوند.
</p>

---

## 18. مقدارهای مهم برای هماهنگی

<p dir="rtl" align="right">
این مقدارها باید با هم هماهنگ باشند:
</p>

<p dir="rtl" align="right">
روی سرور:
</p>

```bash
EXIT_NODE_PSK="MHR-Private-Relay-Key-2026"
```

<p dir="rtl" align="right">
داخل Google Apps Script:
</p>

```js
const AUTH_KEY = "MHR-Private-Relay-Key-2026";
```

<p dir="rtl" align="right">
داخل Setup Wizard کلاینت:
</p>

```text
auth_key: MHR-Private-Relay-Key-2026
```

<p dir="rtl" align="right">
برای Health:
</p>

<p dir="rtl" align="right">
روی سرور:
</p>

```bash
HEALTH_KEY="CHANGE_ME_TO_YOUR_HEALTH_KEY"
```

<p dir="rtl" align="right">
داخل Setup Wizard کلاینت:
</p>

```text
exit_node_health_key: CHANGE_ME_TO_YOUR_HEALTH_KEY
```

<p dir="rtl" align="right">
آدرس Health:
</p>

```text
http://YOUR_VPS_IP:8081
```

---

## 19. نمونه نصب کامل کلاینت

<p dir="rtl" align="right">
داخل پوشه پروژه CMD باز کنید:
</p>

```bat
python -m pip install -r requirements.txt
```

<p dir="rtl" align="right">
سپس اجرا کنید:
</p>

```bat
main.py
```

<p dir="rtl" align="right">
اگر مرورگر خودکار باز نشد:
</p>

```text
http://127.0.0.1:9099/setup
```

<p dir="rtl" align="right">
در Setup Wizard وارد کنید:
</p>

```text
Deployment ID: AKfycbxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
auth_key: MHR-Private-Relay-Key-2026
Download folder: downloads
Exit Node Health URL: http://YOUR_VPS_IP:8081
Exit Node Health Key: CHANGE_ME_TO_YOUR_HEALTH_KEY
```

<p dir="rtl" align="right">
سپس روی دکمه نهایی بزنید:
</p>

```text
ساخت config.json و راه‌اندازی
```

<p dir="rtl" align="right">
بعد از چند ثانیه Dashboard باز می‌شود:
</p>

```text
http://127.0.0.1:9099
```

---

## 20. بررسی نهایی

<p dir="rtl" align="right">
بعد از نصب موفق، این موارد باید درست باشند:
</p>

| Check | Expected |
|---|---|
| `requirements.txt` | نصب شده باشد |
| `config.json` | ساخته شده باشد |
| Dashboard | روی `9099` باز شود |
| HTTP Proxy | روی `8085` فعال باشد |
| SOCKS5 Proxy | روی `1080` فعال باشد |
| Deployment ID | حداقل یک ID ثبت شده باشد |
| auth_key | با Google Apps Script و سرور یکی باشد |
| Exit Node Health | اگر تنظیم شده، در Dashboard آنلاین باشد |

<p dir="rtl" align="right">
اگر این موارد درست باشند، کلاینت آماده استفاده است.
</p>
