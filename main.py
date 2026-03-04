import discord
import os
import json
import requests
import datetime
import asyncio
from discord.ext import commands, tasks
from dotenv import load_dotenv

# --- CONFIG ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
WEATHER_KEY = os.getenv('WEATHER_KEY')

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "data.json"


# --- DATA HELPERS ---
def load_data():
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


# --- QUEST LOGIC ---
def get_weather_and_city(location):
    # If it's a 5-digit zip, force US to avoid international confusion
    query = f"{location},US" if (location.isdigit() and len(location) == 5) else location
    url = f"http://api.openweathermap.org/data/2.5/weather?q={query}&appid={WEATHER_KEY}&units=imperial"
    resp = requests.get(url).json()
    return resp


def generate_quest_text(resp, interests):
    weather = resp["weather"][0]["main"]
    temp = resp["main"]["temp"]
    city = resp["name"]

    # Simple Interest Matching
    interest_list = [i.strip().lower() for i in interests.split(",")]

    if "Rain" in weather or "Snow" in weather:
        return f"It's rainy in {city}. 🌧️ Side Quest: Find a local indoor spot (library or cafe) and spend 15 minutes learning about {interest_list[0]}."
    elif temp > 80:
        return f"Hot day in {city} ({temp}°F)! ☀️ Side Quest: Find a local shop that sells cold treats and try their most unique flavor."
    else:
        return f"Beautiful day in {city}! 🧭 Side Quest: Head toward the nearest park. Find something that relates to {interest_list[0]} and take a photo of it!"


# --- COMMANDS ---

@bot.command()
async def register(ctx):
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    await ctx.send(f"👋 **Guild Registration for {ctx.author.name} is starting!**")
    await ctx.send("📍 What is your **City, State** (e.g. San Diego, CA) or **Zip Code**?")

    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        loc_input = msg.content
        resp = get_weather_and_city(loc_input)

        if resp.get("cod") != 200:
            return await ctx.send("❌ Location not found. Try `!register` again with 'City, State' or Zip.")

        city_name = resp.get("name")
        tz_offset = resp.get("timezone", 0)
    except asyncio.TimeoutError:
        return await ctx.send("⏳ Timed out.")

    await ctx.send(
        f"🗺️ Confirmed: **{city_name}**. Now, what are your **Interests**? (List them like: Art, Coffee, History)")
    try:
        msg = await bot.wait_for('message', check=check, timeout=60.0)
        ints = msg.content
    except asyncio.TimeoutError:
        return await ctx.send("⏳ Timed out.")

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
    await ctx.send(f"✅ **Success!** You are registered in {city_name}. Type `!quest` to get your first mission.")


@bot.command()
async def quest(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid not in users:
        return await ctx.send("Register first with `!register`.")

    user = users[uid]
    today = datetime.date.today().isoformat()

    if user["active_quest"]:
        embed = discord.Embed(title="⚔️ QUEST ALREADY ACTIVE", description=user["active_quest"], color=0xffa500)
        embed.set_footer(text="Complete it with !complete (attach a photo) or !abandon it.")
        return await ctx.send(embed=embed)

    if user["last_quest_date"] == today:
        return await ctx.send("⌛ You've already received a quest today! Come back tomorrow for a new one.")

    resp = get_weather_and_city(user["location"])
    new_q = generate_quest_text(resp, user["interests"])

    users[uid]["active_quest"] = new_q
    users[uid]["last_quest_date"] = today
    save_data(users)

    await ctx.send(embed=discord.Embed(title="📜 NEW SIDE QUEST", description=new_q, color=0x3498db))


@bot.command()
async def complete(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid not in users or not users[uid]["active_quest"]:
        return await ctx.send("You don't have an active quest to complete!")

    if not ctx.message.attachments:
        return await ctx.send("❌ **Evidence Required!** You must attach a photo of your completed quest to earn XP.")

    users[uid]["xp"] += 50
    users[uid].update({"active_quest": None})

    # Level Up Logic
    new_level = (users[uid]["xp"] // 100) + 1
    leveled_up = new_level > users[uid]["level"]
    users[uid]["level"] = new_level

    save_data(users)

    msg = f"🌟 **Quest Completed!** {ctx.author.name} earned 50 XP."
    if leveled_up:
        msg += f"\n🎊 **LEVEL UP!** You reached Level {new_level}!"
    await ctx.send(msg)


@bot.command()
async def profile(ctx):
    users = load_data()
    u = users.get(str(ctx.author.id))
    if not u: return await ctx.send("No profile found. Use `!register`.")

    embed = discord.Embed(title=f"🛡️ {u['name']}'s Stats", color=0x9b59b6)
    embed.add_field(name="Level", value=u["level"], inline=True)
    embed.add_field(name="XP", value=f"{u['xp']}/{(u['level']) * 100}", inline=True)
    embed.add_field(name="Location", value=u["city"], inline=False)
    embed.add_field(name="Current Quest", value=u["active_quest"] or "None", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def abandon(ctx):
    users = load_data()
    uid = str(ctx.author.id)
    if uid in users and users[uid]["active_quest"]:
        users[uid]["active_quest"] = None
        save_data(users)
        await ctx.send("🏳️ Quest abandoned. You can request a new one tomorrow.")
    else:
        await ctx.send("You don't have an active quest.")


# --- HELP ---
bot.remove_command('help')


@bot.command()
async def help(ctx):
    embed = discord.Embed(title="⚔️ SIDE QUEST: HOW TO PLAY", color=0x3498db)
    embed.add_field(name="Commands",
                    value="`!register` - Set up profile\n`!quest` - Get daily mission\n`!complete` - Submit photo proof\n`!profile` - Check stats\n`!abandon` - Drop current quest",
                    inline=False)
    embed.set_footer(text="Quests refresh at 9:00 AM local time.")
    await ctx.send(embed=embed)


# --- SCHEDULER ---
@tasks.loop(minutes=30)
async def daily_trigger():
    users = load_data()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    today = datetime.date.today().isoformat()

    for uid, info in users.items():
        # SAFETY GUARD: Skip if the data is incomplete
        if 'tz_offset' not in info:
            continue

        user_hour = (now_utc + datetime.timedelta(seconds=info['tz_offset'])).hour

        if user_hour == 9:
            try:
                user_obj = await bot.fetch_user(int(uid))
                if info["active_quest"]:
                    await user_obj.send(f"🔔 **Reminder:** You have an open quest: {info['active_quest']}")
                elif info["last_quest_date"] != today:
                    resp = get_weather_and_city(info["location"])
                    new_q = generate_quest_text(resp, info["interests"])
                    users[uid]["active_quest"] = new_q
                    users[uid]["last_quest_date"] = today
                    save_data(users)
                    await user_obj.send(f"☀️ **Morning!** Your new quest: {new_q}")
            except:
                pass


@bot.event
async def on_ready():
    print(f"Side Quest Bot Online.")
    if not daily_trigger.is_running():
        daily_trigger.start()


bot.run(TOKEN)