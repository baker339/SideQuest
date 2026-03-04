import discord
import os
import json
import requests
import datetime
import asyncio
import google.generativeai as genai
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- 1. CONFIGURATION ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
WEATHER_KEY = os.getenv('WEATHER_KEY')
GEMINI_KEY = os.getenv('GEMINI_KEY')

# Configure Gemini
genai.configure(api_key=GEMINI_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Path for Railway Persistent Volume
DATA_FILE = "/app/data/data.json" 
# If testing locally, you can change this to "data.json"

# --- 2. DATA HELPERS ---
def load_data():
    if not os.path.exists("/app/data"):
        os.makedirs("/app/data", exist_ok=True)
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

# --- 3. AI & WEATHER ENGINES ---
def get_weather(location):
    query = f"{location},US" if (location.isdigit() and len(location) == 5) else location
    url = f"http://api.openweathermap.org/data/2.5/weather?q={query}&appid={WEATHER_KEY}&units=imperial"
    return requests.get(url).json()

async def generate_ai_quest(city, weather_resp, interests, level):
    weather_desc = weather_resp['weather'][0]['description']
    temp = weather_resp['main']['temp']
    
    prompt = f"""
    Role: Dungeon Master for a real-life RPG 'Side Quest'.
    User Context: Location: {city}, Weather: {weather_desc} at {temp}F, Interests: {interests}, Player Level: {level}.
    Task: Generate a unique real-world mission.
    Rules: 
    1. Must be safe, legal, and doable in 15-30 mins.
    2. Must require taking a photo as proof.
    3. Match the mission to their interests.
    Format:
    Quest Name: [Title]
    Mission: [1-2 sentences of instructions]
    """
    try:
        response = ai_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"AI Error: {e}")
        return "Quest Name: Local Explorer\nMission: Head to the nearest public park and find a unique landmark. Take a photo of it!"

# --- 4. COMMANDS ---

@bot.command()
async def register(ctx):
    def check(m): return m.author == ctx.author and m.channel == ctx.channel
    
    await ctx.send(f"👋 **Guild Registration for {ctx.author.name} is starting!**")
    await ctx.send("📍 What is your **City, State** (e.g. San Diego, CA) or **Zip Code**?")
    
    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        loc_input = msg.content
        resp = get_weather(loc_input)
        
        if resp.get("cod") != 200:
            return await ctx.send("❌ Location not found. Try `!register` again with a valid city/zip.")
        
        city_name = resp.get("name")
        tz_offset = resp.get("timezone", 0)
    except asyncio.TimeoutError: return await ctx.send("⏳ Timed out.")

    await ctx.send(f"🗺️ Confirmed: **{city_name}**. Now, what are your **Interests**?")
    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        ints = msg.content
    except asyncio.TimeoutError: return await ctx.send("⏳ Timed out.")

    users = load_data()
    users[str(ctx.author.id)] = {
        "name": ctx.author.name,
        "location": loc_input,
        "city": city_name,
        "interests": ints,
        "tz_offset": tz_offset,
        "xp": 0,
        "level": 1,
        "active_quest": None,
        "last_quest_date": ""
    }
    save_data(users)
    await ctx.send(f"✅ **Registered!** Type `!quest` to begin your journey.")

@bot.command()
async def quest(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid not in users: return await ctx.send("Use `!register` first!")
    
    user = users[uid]
    today = datetime.date.today().isoformat()

    if user["active_quest"]:
        embed = discord.Embed(title="⚔️ ACTIVE QUEST", description=user["active_quest"], color=0xffa500)
        return await ctx.send(embed=embed)

    if user["last_quest_date"] == today:
        return await ctx.send("⌛ You've already had a quest today! Come back tomorrow.")

    await ctx.send("✨ *Consulting the Guild Oracle for a custom quest...*")
    resp = get_weather(user["location"])
    new_q = await generate_ai_quest(user['city'], resp, user['interests'], user['level'])
    
    users[uid]["active_quest"] = new_q
    users[uid]["last_quest_date"] = today
    save_data(users)
    
    await ctx.send(embed=discord.Embed(title="📜 YOUR SIDE QUEST", description=new_q, color=0x3498db))

@bot.command()
async def complete(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid not in users or not users[uid]["active_quest"]:
        return await ctx.send("You don't have an active quest!")

    if not ctx.message.attachments:
        return await ctx.send("❌ **Proof Required!** Attach a photo to complete the quest.")

    users[uid]["xp"] += 50
    users[uid]["active_quest"] = None
    
    new_level = (users[uid]["xp"] // 100) + 1
    leveled_up = new_level > users[uid]["level"]
    users[uid]["level"] = new_level
    
    save_data(users)
    msg = f"🌟 **Quest Completed!** +50 XP."
    if leveled_up: msg += f"\n🎊 **LEVEL UP!** You are now Level {new_level}!"
    await ctx.send(msg)

@bot.command()
async def profile(ctx):
    users = load_data()
    u = users.get(str(ctx.author.id))
    if not u: return await ctx.send("Register first!")
    
    embed = discord.Embed(title=f"🛡️ {u['name']}'s Stats", color=0x9b59b6)
    embed.add_field(name="Level", value=u["level"], inline=True)
    embed.add_field(name="XP", value=f"{u['xp']}/{u['level']*100}", inline=True)
    embed.add_field(name="Active Quest", value="Yes" if u["active_quest"] else "None", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def abandon(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid in users and users[uid]["active_quest"]:
        users[uid]["active_quest"] = None
        save_data(users)
        await ctx.send("🏳️ Quest abandoned. See you tomorrow.")

# --- 5. HELP & SCHEDULER ---
bot.remove_command('help')
@bot.command()
async def help(ctx):
    embed = discord.Embed(title="⚔️ HOW TO PLAY", color=0x3498db)
    embed.description = "`!register` - Start here\n`!quest` - Get mission\n`!complete` - Submit with photo\n`!profile` - Stats\n`!abandon` - Drop quest"
    await ctx.send(embed=embed)

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
                    await user_obj.send(f"☀️ **New Quest:**\n{new_q}")
            except: pass

@bot.event
async def on_ready():
    print("Side Quest Bot Online.")
    if not daily_trigger.is_running(): daily_trigger.start()

bot.run(TOKEN)
