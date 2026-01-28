import os
import re
from datetime import datetime, timedelta, time as dtime

import pytz
import requests
from bs4 import BeautifulSoup

import discord
from discord.ext import commands, tasks

# =========================
# ENV
# =========================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID")

if not TOKEN:
    raise RuntimeError("Falta DISCORD_BOT_TOKEN en Variables de Railway")
if not CHANNEL_ID:
    raise RuntimeError("Falta REPORT_CHANNEL_ID en Variables de Railway")

CHANNEL_ID = int(CHANNEL_ID)

# =========================
# CONFIG
# =========================
TZ = pytz.timezone("America/Guayaquil")  # Quito
PAIR = "EURUSD"

# Sesiones Quito
SESSION_1_START = dtime(8, 0)
SESSION_1_END   = dtime(15, 45)

SESSION_2_START = dtime(17, 45)
SESSION_2_END   = dtime(21, 0)

# Orange: 1h antes/después
ORANGE_BLOCK_MIN = 60

# =========================
# DISCORD BOT
# =========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# =========================
# HELPERS
# =========================
def split_discord(text, limit=1900):
    chunks, buf = [], ""
    for line in text.splitlines():
        if len(buf) + len(line) + 1 > limit:
            chunks.append(buf)
            buf = line
        else:
            buf = buf + ("\n" if buf else "") + line
    if buf:
        chunks.append(buf)
    return chunks

def ff_cookies_for_quito():
    # Quito UTC-5 => -300
    return {
        "fftimezone": "America/Guayaquil",
        "fftimezoneoffset": "-300",
    }

def fetch_ff_calendar_html(day: datetime) -> str:
    # Ej: jan27.2026
    day_str = day.strftime("%b").lower() + day.strftime("%d") + "." + day.strftime("%Y")
    url = f"https://www.forexfactory.com/calendar?day={day_str}"

    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.forexfactory.com/calendar",
        "Connection": "keep-alive",
    }

    r = requests.get(url, headers=headers, cookies=ff_cookies_for_quito(), timeout=30)
    r.raise_for_status()
    return r.text

def parse_events_from_html(html: str, day: datetime):
    """
    Devuelve lista:
    [{"dt": datetime(TZ), "currency": "USD/EUR", "impact":"red/orange/yellow/unknown", "title": "..."}]
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("tr.calendar__row")

    events = []
    current_time_str = None

    for row in rows:
        # Hora (se repite/queda en blanco en filas continuas)
        tcell = row.select_one("td.calendar__time")
        if tcell:
            ttxt = tcell.get_text(strip=True)
            if ttxt:
                current_time_str = ttxt

        # Moneda
        ccell = row.select_one("td.calendar__currency")
        currency = ccell.get_text(strip=True) if ccell else ""

        # Evento
        ecell = row.select_one("td.calendar__event")
        title = ecell.get_text(" ", strip=True) if ecell else ""

        # Impacto (heurística por fragmentos)
        impact_cell = row.select_one("td.calendar__impact")
        impact = "unknown"
        if impact_cell:
            frag = str(impact_cell).lower()
            if "impact-red" in frag or "high" in frag:
                impact = "red"
            elif "impact-ora" in frag or "impact-orange" in frag or "medium" in frag:
                impact = "orange"
            elif "impact-yel" in frag or "impact-yellow" in frag or "low" in frag:
                impact = "yellow"

        # Saltar si no hay hora real
        if not current_time_str or current_time_str in ["", "All Day", "Tentative"]:
            continue

        # Parse "2:00pm"
        m = re.match(r"(\d{1,2}):(\d{2})(am|pm)", current_time_str.lower())
        if not m:
            continue

        hh = int(m.group(1))
        mm = int(m.group(2))
        ap = m.group(3)
        if ap == "pm" and hh != 12:
            hh += 12
        if ap == "am" and hh == 12:
            hh = 0

        dt_local = TZ.localize(datetime(day.year, day.month, day.day, hh, mm))

        events.append({
            "dt": dt_local,
            "currency": currency,
            "impact": impact,
            "title": title
        })

    return events

def day_sessions(day: datetime):
    d = day.date()
    s1 = (TZ.localize(datetime.combine(d, SESSION_1_START)), TZ.localize(datetime.combine(d, SESSION_1_END)))
    s2 = (TZ.localize(datetime.combine(d, SESSION_2_START)), TZ.localize(datetime.combine(d, SESSION_2_END)))
    return [("Sesión 1", s1), ("Sesión 2", s2)]

def is_holiday_day(relevant_events):
    # simple: si ForexFactory pone "Holiday" en EUR/USD => no operas el día
    for e in relevant_events:
        t = (e["title"] or "").lower()
        if "holiday" in t:
            return True
    return False

def build_report_for_day(day: datetime):
    html = fetch_ff_calendar_html(day)
    events = parse_events_from_html(html, day)

    # SOLO EUR y USD (porque operas EURUSD)
    relevant = [e for e in events if e["currency"] in ["EUR", "USD"]]

    if is_holiday_day(relevant):
        return (
            f"📅 {day.strftime('%a %d %b %Y')} — {PAIR} (Quito)\n"
            f"🚫 No operar: feriado detectado en calendario (EUR/USD)."
        )

    lines = [f"📅 {day.strftime('%a %d %b %Y')} — {PAIR} (Quito)"]

    for sname, (start, end) in day_sessions(day):
        # 🔴 Si hay RED dentro de la sesión => cancelas la sesión completa
        red_in_session = any(e["impact"] == "red" and start <= e["dt"] <= end for e in relevant)
        if red_in_session:
            lines.append(
                f"🔴 {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: 🚫 NO operar (Red news dentro de la sesión)"
            )
            continue

        # 🟠 Orange: bloquear 1h antes/después
        blocks = []
        for e in relevant:
            if e["impact"] == "orange":
                bstart = e["dt"] - timedelta(minutes=ORANGE_BLOCK_MIN)
                bend   = e["dt"] + timedelta(minutes=ORANGE_BLOCK_MIN)
                if not (bend < start or bstart > end):
                    blocks.append((max(bstart, start), min(bend, end), e))

        # Ventanas operables quitando bloques
        windows = [(start, end)]
        for bstart, bend, _ in sorted(blocks, key=lambda x: x[0]):
            new_windows = []
            for wstart, wend in windows:
                if bend <= wstart or bstart >= wend:
                    new_windows.append((wstart, wend))
                else:
                    if wstart < bstart:
                        new_windows.append((wstart, bstart))
                    if bend < wend:
                        new_windows.append((bend, wend))
            windows = new_windows

        # quitar ventanas < 10 min
        windows = [(a, b) for a, b in windows if (b - a).total_seconds() >= 600]

        if not windows:
            lines.append(
                f"🟠 {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: 🚫 NO operar (bloqueos Orange cubren la sesión)"
            )
        else:
            win_txt = ", ".join([f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}" for a, b in windows])
            lines.append(
                f"✅ {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: Operable en {win_txt}"
            )

    if relevant:
        lines.append("\n🗞️ Eventos EUR/USD del día:")
        for e in relevant:
            icon = "🔴" if e["impact"] == "red" else ("🟠" if e["impact"] == "orange" else "⚪")
            lines.append(f"{icon} {e['dt'].strftime('%H:%M')} {e['currency']} — {e['title']}")
    else:
        lines.append("\nℹ️ Sin eventos EUR/USD detectados.")

    lines.append("\nReglas: rojo cancela sesión completa; naranja bloquea 1h antes/después; rollover 15:45–17:45 no operar.")
    return "\n".join(lines)

def build_report_week(start_day: datetime):
    out = []
    for i in range(7):
        d = start_day + timedelta(days=i)
        out.append(build_report_for_day(d))
        out.append("\n" + "─" * 35 + "\n")
    return "\n".join(out)

# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    print(f"✅ Bot conectado: {bot.user}")
    ch = bot.get_channel(CHANNEL_ID)
    print("📢 Canal:", "OK" if ch else "NO ENCONTRADO (REPORT_CHANNEL_ID mal o sin acceso)")
    if ch:
        await ch.send("✅ Bot online. Comandos: `!ping` `!ffhoy` `!ffsemana`")

    if not daily_nextday_report.is_running():
        daily_nextday_report.start()
    if not weekly_report.is_running():
        weekly_report.start()

# CLAVE: asegura que los comandos funcionen SIEMPRE
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

# =========================
# COMMANDS
# =========================
@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def ffhoy(ctx):
    msg = await ctx.send("⏳ Consultando ForexFactory (Quito)…")
    try:
        day = datetime.now(TZ)
        report = build_report_for_day(day)
        for chunk in split_discord(report):
            await ctx.send(f"```{chunk}```")
        await msg.edit(content="✅ Listo.")
    except Exception as e:
        await msg.edit(content="❌ Falló al consultar ForexFactory.")
        await ctx.send(f"```ERROR: {type(e).__name__}: {e}```")

@bot.command()
async def ffsemana(ctx):
    msg = await ctx.send("⏳ Armando reporte semanal (puede tardar)…")
    try:
        day = datetime.now(TZ)
        report = build_report_week(day)
        for chunk in split_discord(report):
            await ctx.send(f"```{chunk}```")
        await msg.edit(content="✅ Listo.")
    except Exception as e:
        await msg.edit(content="❌ Falló el reporte semanal.")
        await ctx.send(f"```ERROR: {type(e).__name__}: {e}```")

# =========================
# SCHEDULED REPORTS
# =========================
@tasks.loop(time=dtime(20, 0, tzinfo=TZ))  # diario 20:00 Quito -> reporte de mañana
async def daily_nextday_report():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    tomorrow = datetime.now(TZ) + timedelta(days=1)
    report = build_report_for_day(tomorrow)
    for chunk in split_discord(report):
        await channel.send(f"```{chunk}```")

@tasks.loop(time=dtime(19, 0, tzinfo=TZ))  # corre diario 19:00, pero enviamos solo domingo
async def weekly_report():
    now = datetime.now(TZ)
    if now.weekday() != 6:  # domingo = 6
        return
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        return
    report = build_report_week(now)
    for chunk in split_discord(report):
        await channel.send(f"```{chunk}```")

# =========================
# RUN
# =========================
bot.run(TOKEN)
