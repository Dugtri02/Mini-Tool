# 🚀 Mini-Tool Personal/Contributor Set-up Guide

> **⚠️ Python Version Notice:**  
> This bot is developed and tested with **Python 3.11.x** (most development on 3.11.4).  
> **Python 3.12.x, 3.13.x, or higher are NOT supported and will likely not work.**  
> Please use Python 3.11.x for best compatibility.

## 1️⃣ Folder Structure

> **Important:**  
> Make sure all files are in the **top-most folder** in your IDE/workspace!  
> Example:
>
> ```
> Mini-Tool/
> ├── main.py
> ├── requirements.txt
> ├── .env
> ├── cogs/
> │   ├── card.py
> │   ├── compass.py
> │   └── ...etc
> └── SETUP.md
> ```
>
> ⚠️ If you downloaded the bot as a ZIP from GitHub, **move all files out of any nested folders** (like `Mini-Tool-main/`) so `main.py` and the `cogs` folder are directly accessible.
>
> 💡 **You are free to add or remove any available cogs from the `cogs/` folder.**  
> The bot will automatically load all `.py` files in `cogs/` on startup, so you can customize which features are enabled.

---

## 2️⃣ Install Requirements

```sh
pip install -r requirements.txt
```

---

## 3️⃣ Enable Privileged Intents

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Select your bot application.
3. Under **Bot** settings, enable:
   - **SERVER MEMBERS INTENT**
   - **MESSAGE CONTENT INTENT**
   - **PRESENCE INTENT**
4. Save changes.

---

## 4️⃣ Add Your Bot Token

1. Create a file named `.env` in the project folder (if it doesn't already exist).
2. Add your bot token in this format:
   ```
   TOKEN = "your-bot-token-here"
   ```

---

## 5️⃣ Run the Bot

You have two options:

**A. Run directly with Python:**
```sh
python main.py
```

**B. Use the Windows batch file:**
```sh
launch.bat
```
> The batch file will:
> - Check if Python 3.11.x is installed and in your PATH
> - Check/install required packages
> - Launch the bot  
>  
> ⚠️ Make sure you have Python 3.11.x installed and added to your system PATH for `launch.bat` to work.

---

**Need help?**  
Join the [support server](https://discord.gg/exwPCtMEsD) or open an issue on GitHub!

---
 