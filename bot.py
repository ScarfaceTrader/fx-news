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
CHANNEL_ID = os.getenv("REPORT_CHANNEL_ID")
TE_API_KEY = os.getenv("TE_API_KEY", "guest:guest")  # puedes dejar guest:guest o poner tu key real

if not TOKEN:
    raise RuntimeError("Falta DISCORD_BOT_TOKEN en Railway Variables")
if not CHANNEL_ID:
    raise RuntimeError("Falta REPORT_CHANNEL_ID en Railway Variables")

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

ORANGE_BLOCK_MIN = 60  # 1h antes y 1h después

# TradingEconomics importance: normalmente 1=Low, 2=Medium, 3=High
# Tu regla: High=RED cancela sesión; Medium=ORANGE bloquea 1h antes/después
RED_IMPORTANCE = 3
ORANGE_IMPORTANCE = 2

# =========================
# DISCORD
# =========================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

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

# =========================
# TRADING ECONOMICS API
# Docs: https://api.tradingeconomics.com/calendar?... (guest:guest existe)
# =========================
def te_get_calendar(day_local: datetime):
    # Pedimos el día completo en UTC para evitar líos y convertimos a Quito
    # Endpoint ejemplo: https://api.tradingeconomics.com/calendar?c=guest:guest&f=json
    # Podemos filtrar por country y/o importancia
    url = "https://api.tradingeconomics.com/calendar"
    params = {
        "c": TE_API_KEY,
        "f": "json",
        # Filtramos países relevantes para EURUSD:
        # Euro Area + United States
        "country": "United States,Euro Area",
        # Pedimos valores para detectar holiday/eventos
        "values": "true",
        # Rango de fechas (día)
        "d1": day_local.strftime("%Y-%m-%d"),
        "d2": day_local.strftime("%Y-%m-%d"),
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        return []
    return data

def parse_te_events_for_day(day_local: datetime):
    """
    Devuelve eventos relevantes para EURUSD con hora en Quito:
    [
      {"dt": datetime(TZ), "currency": "USD|EUR", "impact": "red|orange|other", "title": str, "raw": dict}
    ]
    """
    raw = te_get_calendar(day_local)
    out = []

    for e in raw:
        # Country en TE: "United States", "Euro Area"
        country = (e.get("Country") or "").strip()
        event = (e.get("Event") or "").strip()
        importance = e.get("Importance")

        # Fecha/hora: TE suele dar "Date" como string; a veces viene con zona
        # Intentamos parsear de forma tolerante
        date_str = e.get("Date") or e.get("DateTime") or e.get("Datetime")
        if not date_str:
            continue

        # Parse básico: "2026-01-27T15:00:00" o "2026-01-27 15:00"
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                dt = datetime.strptime(date_str[:len(fmt)], fmt)
                break
            except Exception:
                pass
        if dt is None:
            continue

        # Asumimos que TE devuelve UTC si no especifica TZ (suele ser así).
        # Convertimos UTC -> Quito.
        dt_utc = pytz.utc.localize(dt)
        dt_local = dt_utc.astimezone(TZ)

        # Reducimos a EUR/USD:
        if country == "United States":
            currency = "USD"
        elif country == "Euro Area":
            currency = "EUR"
        else:
            continue

        impact = "other"
        if importance == RED_IMPORTANCE:
            impact = "red"
        elif importance == ORANGE_IMPORTANCE:
            impact = "orange"

        out.append({
            "dt": dt_local,
            "currency": currency,
            "impact": impact,
            "title": event or "(sin título)",
            "raw": e,
        })

    # Orden por hora
    out.sort(key=lambda x: x["dt"])
    return out

def sessions_for_day(day_local: datetime):
    d = day_local.date()
    s1 = (TZ.localize(datetime.combine(d, SESSION_1_START)), TZ.localize(datetime.combine(d, SESSION_1_END)))
    s2 = (TZ.localize(datetime.combine(d, SESSION_2_START)), TZ.localize(datetime.combine(d, SESSION_2_END)))
    return [("Sesión 1", s1), ("Sesión 2", s2)]

def is_holiday(events):
    # Heurística simple: si el evento contiene "Holiday" en EUR/USD => no operas el día.
    for e in events:
        if "holiday" in (e["title"] or "").lower():
            return True
    return False

def build_report_for_day(day_local: datetime):
    events = parse_te_events_for_day(day_local)

    # Si hay holiday relevante (EUR o USD), no operas ese día
    if is_holiday(events):
        return f"📅 {day_local.strftime('%a %d %b %Y')} — {PAIR} (Quito)\n🚫 No operar: feriado/holiday detectado (EUR/USD)."

    lines = [f"📅 {day_local.strftime('%a %d %b %Y')} — {PAIR} (Quito)"]

    for sname, (start, end) in sessions_for_day(day_local):
        # 🔴 Red dentro de la sesión => cancelas sesión completa
        red_in_session = any(e["impact"] == "red" and start <= e["dt"] <= end for e in events)
        if red_in_session:
            lines.append(f"🔴 {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: 🚫 NO operar (Red news dentro de la sesión)")
            continue

        # 🟠 Orange: bloquea 1h antes y 1h después (dentro de la sesión)
        blocks = []
        for e in events:
            if e["impact"] == "orange":
                bstart = e["dt"] - timedelta(minutes=ORANGE_BLOCK_MIN)
                bend = e["dt"] + timedelta(minutes=ORANGE_BLOCK_MIN)
                if not (bend < start or bstart > end):
                    blocks.append((max(bstart, start), min(bend, end), e))

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

        # elimina ventanas pequeñas (<10 min)
        windows = [(a, b) for a, b in windows if (b - a).total_seconds() >= 600]

        if not windows:
            lines.append(f"🟠 {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: 🚫 NO operar (Orange bloquea toda la sesión)")
        else:
            win_txt = ", ".join([f"{a.strftime('%H:%M')}–{b.strftime('%H:%M')}" for a, b in windows])
            lines.append(f"✅ {sname} {start.strftime('%H:%M')}–{end.strftime('%H:%M')}: Operable en {win_txt}")

    # Lista de eventos EUR/USD
    if events:
        lines.append("\n🗞️ Eventos EUR/USD del día:")
        for e in events:
            icon = "🔴" if e["impact"] == "red" else ("🟠" if e["impact"] == "orange" else "⚪")
            lines.append(f"{icon} {e['dt'].strftime('%H:%M')} {e['currency']} — {e['title']}")
    else:
        lines.append("\nℹ️ Sin eventos EUR/USD detectados para ese día.")

    lines.append("\nReglas: rojo cancela sesión completa; naranja bloquea 1h antes/después; rollover 15:45–17:45 no operar.")
    return "\n".join(lines)

def build_report_week(start_day_local: datetime):
    out = []
    for i in range(7):
        d = start_day_local + timedelta(days=i)
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
    if ch:
        await ch.send("✅ Bot online (TradingEconomics). Comandos: `!ping` `!ffhoy` `!ffsemana`")
    if not daily_nextday_report.is_running():
        daily_nextday_report.start()
    if not weekly_report.is_running():
        weekly_report.start()

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
    msg = await ctx.send("⏳ Generando reporte (hoy)…")
    try:
        day = datetime.now(TZ)
        report = build_report_for_day(day)
        for chunk in split_discord(report):
            await ctx.send(f"```{chunk}```")
        await msg.edit(content="✅ Listo.")
    except Exception as e:
        await msg.edit(content="❌ Falló el reporte.")
        await ctx.send(f"```ERROR: {type(e).__name__}: {e}```")

@bot.command()
async def ffsemana(ctx):
    msg = await ctx.send("⏳ Generando reporte semanal (puede tardar)…")
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

@tasks.loop(time=dtime(19, 0, tzinfo=TZ))  # corre diario 19:00, pero solo envía domingo
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

bot.run(TOKEN)
