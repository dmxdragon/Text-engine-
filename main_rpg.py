"""
main_rpg.py
───────────
Entry point for Nixon RPG Bot — Living World Engine.

Environment Variables:
  DISCORD_TOKEN_RPG   — RPG Bot token
  AIMLAPI_KEY         — AI API key
  MORALIS_API_KEY     — for !verify (NFT ownership check)
  NFT_CONTRACT        — your NFT collection contract address
  NFT_CHAIN           — chain ID (default: 0x1 = Ethereum)
"""

import asyncio
import os
import discord
from discord.ext import commands

# load .env if exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from rpg_engine_v5 import bot as rpg_bot
import world_map as world_map_module

world_map_module.setup(rpg_bot)

async def main():
    token = os.getenv("DISCORD_TOKEN_RPG")
    if not token:
        print("❌ DISCORD_TOKEN_RPG is not set!")
        return
    print("⚔️  Starting Nixon RPG Bot...")
    try:
        await rpg_bot.start(token)
    except discord.LoginFailure:
        print("❌ Invalid DISCORD_TOKEN_RPG.")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
