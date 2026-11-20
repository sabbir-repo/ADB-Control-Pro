# ADB Phone Control — Pro Desktop UI & Terminal Tool

Android Debug Bridge (`adb`) এবং `scrcpy`-এর ওপর ভিত্তি করে তৈরি একটি পূর্ণাঙ্গ Desktop GUI (`tkinter`) এবং Terminal TUI (`rich`) কন্ট্রোল টুল।

---

## পূর্বশর্ত (Prerequisites)
১. **Python 3.10+** ইনস্টল করা থাকতে হবে।
২. **Android Platform Tools (`adb`)** ইনস্টল করে সিস্টেমের `PATH`-এ যোগ থাকতে হবে।
৩. *(ঐচ্ছিক)* স্ক্রিন মিররিংয়ের জন্য **scrcpy** ইনস্টল করুন:
   ```powershell
   winget install Genymobile.scrcpy
   ```

---

## ইনস্টলেশন (Setup)
```powershell
pip install -r requirements.txt
```
> **নোট:** ডেস্কটপ GUI (`ui.py`) চালাতে শুধু Python-এর বিল্ট-ইন `tkinter` যথেষ্ট। `rich` (CLI মোডের জন্য) এবং `qrcode` + `zeroconf` (WiFi QR Pairing-এর জন্য) প্রয়োজন।

---

## ব্যবহার পদ্ধতি (Usage)

### ১. ডেস্কটপ UI চালু করতে:
```powershell
python ui.py
```
অথবা:
```powershell
python main.py --ui
```

### ২. টার্মিনাল (CLI) মোডে চালু করতে:
```powershell
python main.py
```

---

## মূল ফিচারসমূহ
* **Universal Device Selector:** ওপরের *Active Device* ড্রপডাউন থেকে যেকোনো USB বা WiFi ডিভাইস সিলেক্ট করুন।
* **1-Click Smart Unlock (`🔑 Profiles`):** প্রতিটি ফোনের ইউনিক `ro.build.fingerprint` অনুযায়ী PIN, Password, Pattern (`1-9` বা `X,Y`) অথবা Custom Shell সিকোয়েন্স সেভ করে এক ক্লিকে আনলক করুন।
* **App Management (`📦 Apps`):** শুধুমাত্র Launcher আইকনযুক্ত অ্যাপগুলোর লিস্ট (`App Name` + `Package Name`), ১-ক্লিক প্যাকেজ পিকার, APK ইনস্টল/ব্যাচ ইনস্টল, ব্যাকআপ, ও আনইনস্টল।
* **Unlimited File Transfer & Auto-Sync (`📁 Files`):** বড় ফাইল কোনো টাইমআউট ছাড়াই Push/Pull (লাইভ `%`, Speed ও ETA সহ), ফোল্ডার ব্যাকআপ ও **Auto-Sync Watch**।
* **Screen Mirroring (`🖥️ Screen Share`):** `scrcpy` প্রিসেট, Screen-Off মোড (`-S -w`, `Alt+O`), মাল্টি-ডিভাইস মিররিং ও রেকর্ডিং।
* **WiFi & Live Tools (`📶 WiFi & Live`):** গ্রাফিক্যাল QR Code পেয়ারিং উইজার্ড, Quick USB → WiFi সেটআপ, এবং রিয়েল-টাইম Logcat স্ট্রিমার।
