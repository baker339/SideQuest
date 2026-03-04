import discord
import os
import requests
import datetime
import asyncio
import google.generativeai as genai
import certifi
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- 1. CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
WEATHER_KEY = os.getenv('WEATHER_KEY')
GEMINI_KEY = os.getenv('GEMINI_KEY')
MONGO_URI = os.getenv('MONGO_URI')

# AI Setup
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-2.5-flash-lite')

# --- 2. OFFICIAL PYMONGO SETUP ---
# Using the exact setup you provided for Python 3.13 compatibility
client = MongoClient(MONGO_URI, server_api=ServerApi('1'), tlsCAFile=certifi.where())
db = client["side_quest_db"]
users_col = db["users"]

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)


# --- 3. HELPER FOR ASYNC ---
# This allows us to use synchronous pymongo without blocking the bot's heartbeats
async def run_db(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


# --- 4. ENGINES ---
def get_weather(location):
    url = f"http://api.openweathermap.org/data/2.5/weather?q={location}&appid={WEATHER_KEY}&units=imperial"
    try:
        return requests.get(url, timeout=5).json()
    except:
        return {"cod": 404}


async def generate_ai_quest(city, weather_resp, interests, level):
    weather_desc = weather_resp.get('weather', [{}])[0].get('description', 'clear sky')
    temp = weather_resp.get('main', {}).get('temp', 70)
    prompt = (
        f"You are a Dungeon Master. Create a real-life mission for a player in {city}. "
        f"Weather: {weather_desc}, {temp}F. Interests: {interests}. Level: {level}. "
        "The mission must be safe, legal, and require a photo as proof. "
        "Moreover, the mission should be simple, unique, achievable in a day, and encourage exploration, creativity, or getting the player out of their comfort zone. "
        "Format: Quest Name: [Title] Mission: [Instruction]"
    )
    try:
        response = ai_model.generate_content(prompt)
        return response.text
    except:
        return "Quest Name: The Local Scout\nMission: Find unique street art and take a photo."


# --- 5. SLASH COMMANDS ---

@bot.tree.command(name="register", description="Set up profile for THIS server (Private)")
async def register(interaction: discord.Interaction, location: str, interests: str):
    await interaction.response.defer(ephemeral=True)
    resp = get_weather(location)
    if resp.get("cod") != 200:
        return await interaction.followup.send("❌ Location not found.", ephemeral=True)

    composite_id = f"{interaction.guild_id}_{interaction.user.id}"
    user_data = {
        "guild_id": interaction.guild_id,
        "user_id": interaction.user.id,
        "name": interaction.user.name,
        "location": location,
        "city": resp.get("name"),
        "interests": interests,
        "tz_offset": resp.get("timezone", 0),
        "xp": 0, "level": 1, "active_quest": None, "last_quest_date": ""
    }

    await run_db(lambda: users_col.update_one({"_id": composite_id}, {"$set": user_data}, upsert=True))
    await interaction.followup.send(f"✅ Registered in **{resp.get('name')}** for this server!", ephemeral=True)


@bot.tree.command(name="quest", description="Get your daily mission (Private)")
async def quest(interaction: discord.Interaction):
    composite_id = f"{interaction.guild_id}_{interaction.user.id}"
    user = await run_db(lambda: users_col.find_one({"_id": composite_id}))

    if not user:
        return await interaction.response.send_message("Use `/register` first!", ephemeral=True)

    today = datetime.date.today().isoformat()
    if user.get("active_quest"):
        return await interaction.response.send_message(f"⚔️ **Active Quest:**\n{user['active_quest']}", ephemeral=True)
    if user.get("last_quest_date") == today:
        return await interaction.response.send_message("⌛ One quest per day!", ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    resp = get_weather(user["location"])
    new_q = await generate_ai_quest(user['city'], resp, user['interests'], user['level'])

    await run_db(lambda: users_col.update_one({"_id": composite_id},
                                              {"$set": {"active_quest": new_q, "last_quest_date": today}}))
    await interaction.followup.send(embed=discord.Embed(title="📜 SIDE QUEST", description=new_q, color=0x3498db),
                                    ephemeral=True)


@bot.tree.command(name="complete", description="Submit your photo proof (Public)")
async def complete(interaction: discord.Interaction, photo: discord.Attachment):
    composite_id = f"{interaction.guild_id}_{interaction.user.id}"
    user = await run_db(lambda: users_col.find_one({"_id": composite_id}))

    if not user or not user.get("active_quest"):
        return await interaction.response.send_message("No active quest!", ephemeral=True)

    new_xp = user["xp"] + 50
    new_level = (new_xp // 100) + 1
    leveled_up = new_level > user["level"]

    await run_db(lambda: users_col.update_one(
        {"_id": composite_id},
        {"$set": {"xp": new_xp, "level": new_level, "active_quest": None}}
    ))

    embed = discord.Embed(title="🌟 QUEST COMPLETE!", color=0x2ecc71)
    embed.description = f"**{interaction.user.name}** earned **50 XP** in this server!"
    embed.set_image(url=photo.url)
    if leveled_up: embed.add_field(name="🎊 LEVEL UP", value=f"Reached Level {new_level}!")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="Server rankings")
async def leaderboard(interaction: discord.Interaction):
    def get_top():
        cursor = users_col.find({"guild_id": interaction.guild_id}).sort([("level", -1), ("xp", -1)]).limit(10)
        return list(cursor)

    top_users = await run_db(get_top)

    embed = discord.Embed(title=f"🏆 {interaction.guild.name} LEADERBOARD", color=0xf1c40f)
    text = ""
    for i, u in enumerate(top_users):
        medal = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"**{i + 1}.**"
        text += f"{medal} **{u['name']}** - Lvl {u['level']} ({u['xp']} XP)\n"

    embed.description = text if text else "No adventurers yet."
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="profile", description="Check your stats for this server")
async def profile(interaction: discord.Interaction):
    composite_id = f"{interaction.guild_id}_{interaction.user.id}"
    user = await run_db(lambda: users_col.find_one({"_id": composite_id}))
    if not user: return await interaction.response.send_message("Register first!", ephemeral=True)

    embed = discord.Embed(title=f"🛡️ {user['name']}'s Stats", color=0x9b59b6)
    embed.add_field(name="Level", value=user["level"], inline=True)
    embed.add_field(name="XP", value=f"{user['xp']}/{user['level'] * 100}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="abandon", description="Give up on your current quest")
async def abandon(interaction: discord.Interaction):
    composite_id = f"{interaction.guild_id}_{interaction.user.id}"
    await run_db(lambda: users_col.update_one({"_id": composite_id}, {"$set": {"active_quest": None}}))
    await interaction.response.send_message("🏳️ Quest Abandoned. You can try again tomorrow.", ephemeral=True)


@bot.tree.command(name="help", description="How to play Side Quest")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="⚔️ ADVENTURER'S GUIDE", color=0x3498db)
    embed.add_field(name="📜 Commands", value=(
        "`/register` - Setup your city and interests (Private)\n"
        "`/quest` - Get a daily real-world mission (Private)\n"
        "`/complete` - Post your photo proof to earn XP (Public)\n"
        "`/profile` - Check your Level and XP for this server\n"
        "`/leaderboard` - See top players in this server\n"
        "`/abandon` - Cancel your current mission"
    ))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 6. SETUP ---
@bot.event
async def on_ready():
    try:
        client.admin.command('ping')
        print("✅ Successfully connected to MongoDB using official driver!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")

    print(f"Logged in as {bot.user}")
    await bot.tree.sync()


bot.run(TOKEN)
