import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "0"))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot conectado: {bot.user}")
    if CHANNEL_ID:
        ch = bot.get_channel(CHANNEL_ID)
        print("📢 Canal:", "OK" if ch else "NO ENCONTRADO")
        if ch:
            await ch.send("✅ Estoy online y puedo escribir aquí. Prueba: `!ping`")

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

bot.run(TOKEN)
