import discord, os, asyncio, logging; from discord.ext import commands

# Set up logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s'
)
logger = logging.getLogger()

intents = discord.Intents.all(); bot = commands.Bot(command_prefix="!", intents=intents); bot.remove_command('help')

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user}')
    # Load all cogs that aren't already loaded
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            cog_name = f'cogs.{filename[:-3]}'
            if cog_name not in bot.extensions:
                try:
                    await bot.load_extension(cog_name)
                    logger.info(f'Loaded cog: {filename[:-3]}')
                    await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f'Failed to load cog {filename[:-3]}: {e}')
    await bot.tree.sync()
    
bot.run('TOKEN') # Replace 'TOKEN' with your bot token as a String or Variable, I recommend using an .env variable

# If you're contributing to the repo do not commit the bot.py file, especially if you left the TOKEN exposed