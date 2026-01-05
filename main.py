import discord
from discord import app_commands
import requests
import os
import json
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime
import time
from functools import lru_cache
from collections import defaultdict

# ============================
# ENVIRONMENT SETUP
# ============================
if os.path.exists('.env'):
    load_dotenv()

JSONBIN_URL = os.getenv("JSONBIN_URL")
JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY")
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
# Parse multiple developer IDs
DEV_IDS_STR = os.getenv("DEV_DISCORD_ID", "0")
DEV_IDS = [int(id.strip()) for id in DEV_IDS_STR.split(",") if id.strip().isdigit()]
ALLOWED_CHANNEL = int(os.getenv("ALLOWED_CHANNEL", "0"))

# Validate required environment variables
required_vars = {
    "JSONBIN_URL": JSONBIN_URL,
    "JSONBIN_API_KEY": JSONBIN_API_KEY,
    "DISCORD_BOT_TOKEN": BOT_TOKEN
}

missing_vars = [var for var, value in required_vars.items() if not value]
if missing_vars:
    raise Exception(f"Missing environment variables: {', '.join(missing_vars)}")

JSONBIN_HEADERS = {
    "Content-Type": "application/json",
    "X-Master-Key": JSONBIN_API_KEY,
    "X-Bin-Meta": "false"
}

WHITELIST_PAUSED = False

# Rate limiting (per user, per minute)
user_rate_limits = defaultdict(list)
RATE_LIMIT_WINDOW = 60  # 1 minute
RATE_LIMIT_MAX = 10     # 10 commands per minute per user

# ============================
# JSONBIN.IO FUNCTIONS (OPTIMIZED WITH CACHING)
# ============================
import time
from functools import lru_cache

# Cache for whitelist data (5 minute TTL)
_whitelist_cache = {"data": None, "timestamp": 0}
CACHE_TTL = 300  # 5 minutes

def get_whitelist_data(force_refresh=False):
    """Fetch whitelist data from JSONBin.io with caching"""
    current_time = time.time()
    
    # Check cache first (unless force refresh)
    if not force_refresh and _whitelist_cache["data"] is not None:
        if current_time - _whitelist_cache["timestamp"] < CACHE_TTL:
            return _whitelist_cache["data"]
    
    try:
        response = requests.get(JSONBIN_URL, headers=JSONBIN_HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Ensure we always return an array
            if isinstance(data, list):
                # Update cache
                _whitelist_cache["data"] = data
                _whitelist_cache["timestamp"] = current_time
                return data
            else:
                return []
        else:
            print(f"Error fetching data: {response.status_code}")
            # Return cached data if available, even if expired
            return _whitelist_cache["data"] if _whitelist_cache["data"] is not None else []
    except Exception as e:
        print(f"Error fetching from JSONBin: {e}")
        # Return cached data if available
        return _whitelist_cache["data"] if _whitelist_cache["data"] is not None else []

def update_whitelist_data(data):
    """Update whitelist data on JSONBin.io and invalidate cache"""
    try:
        response = requests.put(JSONBIN_URL, headers=JSONBIN_HEADERS, json=data, timeout=10)
        success = response.status_code == 200
        if success:
            # Update cache with new data
            _whitelist_cache["data"] = data
            _whitelist_cache["timestamp"] = time.time()
        return success
    except Exception as e:
        print(f"Error updating JSONBin: {e}")
        return False

def get_uid_entry(uid):
    """Get specific UID entry from whitelist"""
    data = get_whitelist_data()
    for entry in data:
        if entry.get("uid") == uid:
            return entry
    return None

def add_uid_entry(uid, expiry, comment):
    """Add or update UID entry with validation"""
    # Input validation
    if not uid or not uid.strip():
        return False
    
    # Validate date format
    try:
        datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError:
        return False
    
    data = get_whitelist_data(force_refresh=True)  # Force refresh for updates
    
    # Check if UID already exists
    existing_index = -1
    for i, entry in enumerate(data):
        if entry.get("uid") == uid:
            existing_index = i
            break
    
    new_entry = {
        "uid": uid.strip(),
        "expiry_date": expiry,
        "comment": comment.strip() if comment else ""
    }
    
    if existing_index >= 0:
        # Update existing entry
        data[existing_index] = new_entry
    else:
        # Add new entry
        data.append(new_entry)
    
    return update_whitelist_data(data)

def remove_uid_entry(uid):
    """Remove UID entry with validation"""
    if not uid or not uid.strip():
        return False
        
    data = get_whitelist_data(force_refresh=True)  # Force refresh for updates
    new_data = [entry for entry in data if entry.get("uid") != uid.strip()]
    
    if len(new_data) != len(data):
        return update_whitelist_data(new_data)
    return False

def get_all_uids():
    """Get all UID entries"""
    return get_whitelist_data()

# ============================
# RATE LIMITING
# ============================
def check_rate_limit(user_id):
    """Check if user is within rate limits"""
    current_time = time.time()
    user_requests = user_rate_limits[user_id]
    
    # Remove old requests outside the window
    user_requests[:] = [req_time for req_time in user_requests if current_time - req_time < RATE_LIMIT_WINDOW]
    
    # Check if under limit
    if len(user_requests) >= RATE_LIMIT_MAX:
        return False
    
    # Add current request
    user_requests.append(current_time)
    return True

# ============================
# ENHANCED LOGGING SYSTEM
# ============================
async def send_log(bot, action: str, uid: str, user: discord.User, expiry: str = None, comment: str = None):
    """Enhanced logging function with formatted messages"""
    if not LOG_CHANNEL_ID:
        return
        
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if not ch:
        return

    # Get current timestamp
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Create formatted log message based on action type
    if action == "ADD":
        embed = discord.Embed(
            title="🟢 UID ADDED",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        embed.add_field(name="UID", value=f"`{uid}`", inline=True)
        embed.add_field(name="Expiry", value=f"`{expiry}`", inline=True)
        embed.add_field(name="Comment", value=f"`{comment}`", inline=True)
        embed.add_field(name="Added By", value=f"`{user.name}`\n(`{user.id}`)", inline=True)
        embed.add_field(name="Timestamp", value=f"`{current_time}`", inline=True)
        embed.set_footer(text="Whitelist System")
        
    elif action == "REMOVE":
        embed = discord.Embed(
            title="🔴 UID REMOVED",
            color=0xff0000,
            timestamp=datetime.now()
        )
        embed.add_field(name="UID", value=f"`{uid}`", inline=True)
        embed.add_field(name="Removed By", value=f"`{user.name}`\n(`{user.id}`)", inline=True)
        embed.add_field(name="Timestamp", value=f"`{current_time}`", inline=True)
        embed.set_footer(text="Whitelist System")
        
    elif action == "PAUSE":
        embed = discord.Embed(
            title="⏸️ SYSTEM PAUSED",
            color=0xffff00,
            timestamp=datetime.now()
        )
        embed.add_field(name="Action By", value=f"`{user.name}`\n(`{user.id}`)", inline=True)
        embed.add_field(name="Timestamp", value=f"`{current_time}`", inline=True)
        embed.set_footer(text="Whitelist System")
        
    elif action == "RESUME":
        embed = discord.Embed(
            title="▶️ SYSTEM RESUMED",
            color=0x00ff00,
            timestamp=datetime.now()
        )
        embed.add_field(name="Action By", value=f"`{user.name}`\n(`{user.id}`)", inline=True)
        embed.add_field(name="Timestamp", value=f"`{current_time}`", inline=True)
        embed.set_footer(text="Whitelist System")
    
    await ch.send(embed=embed)

async def send_simple_log(bot, message: str):
    """Simple text-based log for non-UID actions"""
    if not LOG_CHANNEL_ID:
        return
        
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if ch:
        await ch.send(f"`{datetime.now().strftime('%H:%M:%S')}` {message}")

# ============================
# FORMAT DATE FOR DISPLAY
# ============================
def format_display_date(raw):
    """Format date from YYYY-MM-DD to DD-MM-YYYY for display"""
    try:
        y, m, d = raw.split("-")
        return f"{d}-{m}-{y}"
    except:
        return raw

# ============================
# OWNER CHECK (UPDATED FOR MULTIPLE DEVS)
# ============================
def owner_only():
    async def predicate(interaction):
        if interaction.user.id not in DEV_IDS:
            await interaction.response.send_message(
                "❌ Only **bot owners** can use this command.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

# ============================
# CHANNEL CHECK
# ============================
def channel_only():
    async def predicate(interaction):
        if interaction.channel_id != ALLOWED_CHANNEL:
            await interaction.response.send_message(
                "❌ You can only use commands in the assigned whitelist channel.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

# ============================
# RATE LIMIT CHECK
# ============================
def rate_limit_check():
    async def predicate(interaction):
        if not check_rate_limit(interaction.user.id):
            await interaction.response.send_message(
                "⚠️ Rate limit exceeded. Please wait before using commands again.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)
# ============================
# PAUSE CHECK
# ============================
def pause_check():
    async def predicate(interaction):
        if WHITELIST_PAUSED:
            await interaction.response.send_message(
                "⚠️ Whitelist system is currently **PAUSED**.",
                ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)

# ============================
# FORMAT DATE
# ============================
def format_box_date(raw):
    try:
        y, m, d = raw.split("-")
        return f"{d} - {m} - {y}"
    except:
        return raw

# ============================
# BOT CLASS
# ============================
class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        print(f"[READY] Logged in as {self.user}")
        try:
            cmds = await self.tree.sync()
            print(f"Synced {len(cmds)} commands.")
            # Send startup log
            await send_simple_log(self, "🟢 **Bot Started Successfully**")
        except Exception as e:
            print(f"Error syncing commands: {e}")

    async def setup_hook(self):
        print("[SETUP] Bot is starting up...")

bot = MyBot()

# ============================
# /help (PUBLIC)
# ============================
@bot.tree.command(name="help", description="Show all bot commands")
@channel_only()
async def help_cmd(interaction: discord.Interaction):
    text = (
        "**📘 Whitelist Bot Commands**\n\n"
        "🔵 Public:\n"
        "`/help`\n"
        "`/checkuid <uid>`\n"
        "`/viewuid <uid>`\n"
        "`/listuids`\n"
        "`/adduid <uid> <year> <month> <day> <comment>`\n\n"
        "🟣 Owner Only:\n"
        "`/removeuid <uid>`\n"
        "`/pause`\n"
        "`/resume`\n"
    )
    await interaction.response.send_message(text)

# ============================
# /checkuid (PUBLIC)
# ============================
@bot.tree.command(name="checkuid", description="Check UID JSON")
@channel_only()
@rate_limit_check()
async def checkuid(interaction: discord.Interaction, uid: str):
    try:
        entry = get_uid_entry(uid)
        if not entry:
            await interaction.response.send_message("UID not found.")
            return

        formatted = [{
            "uid": entry["uid"],
            "expiry_date": entry["expiry_date"],
            "comment": entry["comment"]
        }]

        await interaction.response.send_message(
            f"```json\n{json.dumps(formatted, indent=2)}\n```"
        )
    except Exception as e:
        await interaction.response.send_message("❌ Database connection error.")
        return

# ============================
# /viewuid (PUBLIC)
# ============================
@bot.tree.command(name="viewuid", description="UID box view")
@channel_only()
@rate_limit_check()
async def viewuid(interaction: discord.Interaction, uid: str):
    try:
        entry = get_uid_entry(uid)
        if not entry:
            await interaction.response.send_message("UID not found.")
            return

        pretty = format_box_date(entry["expiry_date"])

        box = (
            "```\n"
            "📦 WHITELIST ENTRY\n"
            "──────────────────────────\n"
            f"UID:        {entry['uid']}\n"
            f"Expiry:     {pretty}\n"
            f"Comment:    {entry['comment']}\n"
            "```"
        )
        await interaction.response.send_message(box)
    except Exception as e:
        await interaction.response.send_message("❌ Database connection error.")
        return

# ============================
# /listuids (PUBLIC)
# ============================
@bot.tree.command(name="listuids", description="List all UIDs")
@channel_only()
@rate_limit_check()
async def listuids(interaction: discord.Interaction):
    try:
        data = get_all_uids()
        formatted = [{
            "uid": entry["uid"],
            "expiry_date": entry["expiry_date"],
            "comment": entry["comment"]
        } for entry in data]

        await interaction.response.send_message(
            f"```json\n{json.dumps(formatted, indent=2)}\n```"
        )
    except Exception as e:
        await interaction.response.send_message("❌ Database connection error.")
        return

# ============================
# /adduid (PUBLIC) - OPTIMIZED WITH VALIDATION
# ============================
@bot.tree.command(name="adduid", description="Add a UID entry")
@channel_only()
@pause_check()
@rate_limit_check()
async def adduid(
    interaction: discord.Interaction,
    uid: str,
    year: int,
    month: int,
    day: int,
    comment: str
):
    # Input validation
    if not uid.strip():
        await interaction.response.send_message("❌ UID cannot be empty.", ephemeral=True)
        return
    
    # Date validation
    if year < 2024 or year > 2030:
        await interaction.response.send_message("❌ Year must be between 2024-2030.", ephemeral=True)
        return
    
    if month < 1 or month > 12:
        await interaction.response.send_message("❌ Month must be between 1-12.", ephemeral=True)
        return
    
    if day < 1 or day > 31:
        await interaction.response.send_message("❌ Day must be between 1-31.", ephemeral=True)
        return
    
    # Check if date is valid
    try:
        datetime(year, month, day)
    except ValueError:
        await interaction.response.send_message("❌ Invalid date.", ephemeral=True)
        return
    
    expiry = f"{year:04d}-{month:02d}-{day:02d}"
    
    try:
        # Check if UID already exists
        existing_entry = get_uid_entry(uid.strip())
        
        if existing_entry:
            action = "updated"
        else:
            action = "added"
        
        # Add/update UID
        success = add_uid_entry(uid, expiry, comment)
        
        if success:
            # Send simple confirmation message ONLY to the user who added (ephemeral)
            if action == "added":
                await interaction.response.send_message(f"🟢 UID **{uid.strip()}** added.", ephemeral=True)
            else:
                await interaction.response.send_message(f"🟡 UID **{uid.strip()}** updated.", ephemeral=True)
            
            # Send detailed log to log channel (visible to everyone in log channel)
            await send_log(bot, "ADD", uid.strip(), interaction.user, expiry, comment)
        else:
            await interaction.response.send_message("❌ Error saving UID to database.", ephemeral=True)
            
    except Exception as e:
        print(f"Error in adduid: {e}")
        await interaction.response.send_message("❌ Database connection error.", ephemeral=True)
        return

# ============================
# /removeuid (OWNER ONLY) - UPDATED FOR JSONBIN ARRAY
# ============================
@bot.tree.command(name="removeuid", description="Remove a UID")
@channel_only()
@owner_only()
@pause_check()
async def removeuid(interaction: discord.Interaction, uid: str):
    try:
        success = remove_uid_entry(uid)
        
        if success:
            # Send confirmation only to the user who removed
            await interaction.response.send_message(f"🔴 UID **{uid}** removed.", ephemeral=True)
            # Send log to log channel
            await send_log(bot, "REMOVE", uid, interaction.user)
        else:
            await interaction.response.send_message("❌ UID not found or error removing.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("❌ Database connection error.", ephemeral=True)
        return

# ============================
# /pause (OWNER ONLY)
# ============================
@bot.tree.command(name="pause", description="Pause whitelist")
@channel_only()
@owner_only()
async def pause(interaction: discord.Interaction):
    global WHITELIST_PAUSED
    WHITELIST_PAUSED = True
    
    # Send confirmation only to the user
    await interaction.response.send_message("⏸️ Whitelist PAUSED.", ephemeral=True)
    # Send log to log channel
    await send_log(bot, "PAUSE", "", interaction.user)

# ============================
# /resume (OWNER ONLY)
# ============================
@bot.tree.command(name="resume", description="Resume whitelist")
@channel_only()
@owner_only()
async def resume(interaction: discord.Interaction):
    global WHITELIST_PAUSED
    WHITELIST_PAUSED = False
    
    # Send confirmation only to the user
    await interaction.response.send_message("▶️ Whitelist RESUMED.", ephemeral=True)
    # Send log to log channel
    await send_log(bot, "RESUME", "", interaction.user)

# ============================
# RUN BOT
# ============================
if __name__ == "__main__":
    bot.run(BOT_TOKEN)
