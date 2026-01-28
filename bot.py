import os
import discord
from discord.ext import commands

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Falta DISCORD_BOT_TOKEN en Railway Variables")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot conectado: {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    print(f"📩 Recibí mensaje en #{getattr(message.channel,'name','?')}: {message.content} | de {message.author}")
    await bot.process_commands(message)

@bot.command()
async def ping(ctx):
    print("🏓 Ejecuté comando ping")
    await ctx.send("🏓 Pong!")

bot.run(TOKEN)
