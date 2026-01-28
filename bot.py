import os
from datetime import datetime, timedelta, time as dtime
import pytz
import requests

import discord
from discord.ext import commands, tasks

# =========================
# ENV
# =========================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID"))

# =========================
# CONFIG
# =========================
TZ = pytz.timezone("America/Guayaquil")
PAIR = "EURUSD"

SESSION_1 = (dtime(8,0), dtime(15,45))
SESSION_2 = (dtime(17,45), dtime(21,0))

ORANGE_BLOCK_MIN = 60

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# DATA SOURCE (Investing)
# =========================
def fetch_calendar(date: datetime):
    url = "https://economic-calendar.tradingview.com/events"
    params = {
        "from": date.strftime("%Y-%m-%d"),
        "to": date.strftime("%Y-%m-%d"),
        "countries": "US,EU"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

# =========================
# LOGIC
# =========================
def build_day_report(day: datetime):
    events = fetch_calendar(day)
    lines = [f"📅 {day.strftime('%A %d %b %Y')} — EURUSD (Quito)"]

    sessions = [
        ("Sesión 1", SESSION_1),
        ("Sesión 2", SESSION_2)
    ]

    for name, (s_start, s_end) in sessions:
        start = TZ.localize(datetime.combine(day.date(), s_start))
        end   = TZ.localize(datetime.combine(day.date(), s_end))

        red = False
        blocks = []

        for e in events:
            impact = e.get("importance", 1)
            currency = e.get("country")
            t = datetime.fromtimestamp(e["date"]/1000, TZ)

            if currency not in ["US", "EU"]:
                continue

            if impact == 3 and start <= t <= end:
                red = True

            if impact == 2:
                blocks.append((t - timedelta(minutes=60), t + timedelta(minutes=60)))

        if red:
            lines.append(f"🔴 {name} {s_start}-{s_end}: NO operar (Red news)")
            continue

        windows = [(start, end)]
        for b_start, b_end in blocks:
            new = []
            for w_start, w_end in windows:
                if b_end <= w_start or b_start >= w_end:
                    new.append((w_start, w_end))
                else:
                    if w_start < b_start:
                        new.append((w_start, b_start))
                    if b_end < w_end:
                        new.append((b_end, w_end))
            windows = new

        if windows:
            txt = ", ".join([f"{a.strftime('%H:%M')}-{b.strftime('%H:%M')}" for a,b in windows])
            lines.append(f"✅ {name}: Operable {txt}")
        else:
            lines.append(f"🟠 {name}: NO operar (Orange blocks)")

    return "\n".join(lines)

# =========================
# COMMANDS
# =========================
@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def ffhoy(ctx):
    report = build_day_report(datetime.now(TZ))
    await ctx.send(f"```{report}```")

@bot.command()
async def ffsemana(ctx):
    out = []
    for i in range(7):
        out.append(build_day_report(datetime.now(TZ)+timedelta(days=i)))
        out.append("─"*30)
    await ctx.send(f"```{chr(10).join(out)}```")

# =========================
# AUTO REPORTS
# =========================
@tasks.loop(time=dtime(20,0,tzinfo=TZ))
async def daily_report():
    ch = bot.get_channel(CHANNEL_ID)
    await ch.send(f"```{build_day_report(datetime.now(TZ)+timedelta(days=1))}```")

@tasks.loop(time=dtime(19,0,tzinfo=TZ))
async def weekly_report():
    if datetime.now(TZ).weekday() != 6:
        return
    ch = bot.get_channel(CHANNEL_ID)
    txt = "\n".join(build_day_report(datetime.now(TZ)+timedelta(days=i)) for i in range(7))
    await ch.send(f"```{txt}```")

@bot.event
async def on_ready():
    daily_report.start()
    weekly_report.start()
    print("✅ Bot online")

bot.run(TOKEN)
