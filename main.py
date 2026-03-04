import discord
import os
import json
import requests
import datetime
import asyncio
import google.generativeai as genai
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- 1. CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
WEATHER_KEY = os.getenv('WEATHER_KEY')
GEMINI_KEY = os.getenv('GEMINI_KEY')

# Your preferred AI Setup
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-2.5-flash-lite')  # Using 8b for higher free quota

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Railway Volume Path
DATA_FILE = "data.json"


# --- 2. DATA PERSISTENCE ---
def load_data():
    # if not os.path.exists("/app/data"):
    #     os.makedirs("/app/data", exist_ok=True)
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# --- 3. ENGINES ---
def get_weather(location):
    query = f"{location},US" if (location.isdigit() and len(location) == 5) else location
    url = f"http://api.openweathermap.org/data/2.5/weather?q={query}&appid={WEATHER_KEY}&units=imperial"
    return requests.get(url).json()


async def generate_ai_quest(city, weather_resp, interests, level):
    weather_desc = weather_resp['weather'][0]['description']
    temp = weather_resp['main']['temp']

    prompt = (
        f"You are a Dungeon Master. Create a real-life mission for a player in {city}. "
        f"Weather: {weather_desc}, {temp}F. Interests: {interests}. Level: {level}. "
        "The mission must be safe, legal, and require a photo as proof. "
        "Format: Quest Name: [Title] Mission: [Instruction]"
    )

    try:
        response = ai_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"AI Error: {e}")
        return "Quest Name: The Local Scout\nMission: Find a unique piece of street art or a historical marker and take a photo."


# --- 4. SLASH COMMANDS ---

@bot.tree.command(name="register", description="Set up your profile (Private)")
@app_commands.describe(location="City, State or Zip Code", interests="e.g. Hiking, Coffee, Gaming")
async def register(interaction: discord.Interaction, location: str, interests: str):
    await interaction.response.defer(ephemeral=True)

    resp = get_weather(location)
    if resp.get("cod") != 200:
        return await interaction.followup.send("❌ Location not found. Use 'City, State' or Zip.", ephemeral=True)

    city_name = resp.get("name")
    users = load_data()
    users[str(interaction.user.id)] = {
        "name": interaction.user.name,
        "location": location,
        "city": city_name,
        "interests": interests,
        "tz_offset": resp.get("timezone", 0),
        "xp": 0, "level": 1, "active_quest": None, "last_quest_date": ""
    }
    save_data(users)
    await interaction.followup.send(f"✅ Registered in **{city_name}**! Use `/quest` to start.", ephemeral=True)


@bot.tree.command(name="quest", description="Get your private daily mission")
async def quest(interaction: discord.Interaction):
    users = load_data()
    uid = str(interaction.user.id)
    if uid not in users:
        return await interaction.response.send_message("Use `/register` first!", ephemeral=True)

    user = users[uid]
    today = datetime.date.today().isoformat()

    if user["active_quest"]:
        return await interaction.response.send_message(f"⚔️ **Active Quest:**\n{user['active_quest']}", ephemeral=True)

    if user["last_quest_date"] == today:
        return await interaction.response.send_message("⌛ One quest per day! Try again tomorrow.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    resp = get_weather(user["location"])
    new_q = await generate_ai_quest(user['city'], resp, user['interests'], user['level'])

    users[uid]["active_quest"] = new_q
    users[uid]["last_quest_date"] = today
    save_data(users)

    embed = discord.Embed(title="📜 YOUR SIDE QUEST", description=new_q, color=0x3498db)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="complete", description="Submit your photo proof (Public!)")
@app_commands.describe(photo="Attach your quest photo")
async def complete(interaction: discord.Interaction, photo: discord.Attachment):
    users = load_data()
    uid = str(interaction.user.id)

    if uid not in users or not users[uid]["active_quest"]:
        return await interaction.response.send_message("No active quest to complete!", ephemeral=True)

    # Logic
    users[uid]["xp"] += 50
    users[uid]["active_quest"] = None
    new_level = (users[uid]["xp"] // 100) + 1
    leveled_up = new_level > users[uid]["level"]
    users[uid]["level"] = new_level
    save_data(users)

    # Public Embed
    embed = discord.Embed(title="🌟 QUEST COMPLETE!", color=0x2ecc71)
    embed.description = f"**{interaction.user.name}** just earned **50 XP** for finishing their quest!"
    embed.set_image(url=photo.url)
    if leveled_up:
        embed.add_field(name="🎊 LEVEL UP", value=f"Reached Level {new_level}!")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="profile", description="Check your RPG stats")
async def profile(interaction: discord.Interaction):
    users = load_data()
    u = users.get(str(interaction.user.id))
    if not u: return await interaction.response.send_message("Register first!", ephemeral=True)

    embed = discord.Embed(title=f"🛡️ {u['name']}'s Stats", color=0x9b59b6)
    embed.add_field(name="Level", value=u["level"], inline=True)
    embed.add_field(name="XP", value=f"{u['xp']}/{u['level'] * 100}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="leaderboard", description="View the top quest hunters in the server")
async def leaderboard(interaction: discord.Interaction):
    users = load_data()

    if not users:
        return await interaction.response.send_message(
            "The Guild is currently empty. Start the journey with `/register`!", ephemeral=True)

    # Sort users by Level (descending) and then XP (descending)
    sorted_users = sorted(
        users.values(),
        key=lambda x: (x.get('level', 1), x.get('xp', 0)),
        reverse=True
    )

    embed = discord.Embed(
        title="🏆 SIDE QUEST LEADERBOARD",
        color=0xf1c40f,  # Gold color
        description="The most dedicated adventurers in the realm."
    )

    # Limit to top 10 users
    top_10 = sorted_users[:10]

    leaderboard_text = ""
    for i, user in enumerate(top_10):
        # Add medals for top 3
        rank_emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"**{i + 1}.**"

        name = user.get('name', 'Unknown Explorer')
        lvl = user.get('level', 1)
        xp = user.get('xp', 0)
        quests = user.get('completed_quests', 0)  # Assumes you have this field

        leaderboard_text += f"{rank_emoji} **{name}** - Lvl {lvl} ({xp} XP)\n"

    embed.description = leaderboard_text if leaderboard_text else "No heroes found yet."
    embed.set_footer(text="Keep questing to climb the ranks!")

    # We send this publicly so everyone can see the competition
    await interaction.response.send_message(embed=embed)


# --- 5. SETUP & SYNC ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    # Sync slash commands to Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)
    if not daily_trigger.is_running():
        daily_trigger.start()


@tasks.loop(minutes=30)
async def daily_trigger():
    users = load_data()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today().isoformat()
    for uid, info in users.items():
        if 'tz_offset' not in info: continue
        user_hour = (now_utc + datetime.timedelta(seconds=info['tz_offset'])).hour
        if user_hour == 9:
            try:
                user_obj = await bot.fetch_user(int(uid))
                if info["active_quest"]:
                    await user_obj.send(f"🔔 **Reminder:** Finish your quest!\n{info['active_quest']}")
                elif info["last_quest_date"] != today:
                    resp = get_weather(info["location"])
                    new_q = await generate_ai_quest(info['city'], resp, info['interests'], info['level'])
                    users[uid]["active_quest"] = new_q
                    users[uid]["last_quest_date"] = today
                    save_data(users)
                    await user_obj.send(f"☀️ **Good morning!** Your new quest is ready:\n{new_q}")
            except:
                pass


bot.run(TOKEN)
