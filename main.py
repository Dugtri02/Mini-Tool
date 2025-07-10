import discord, os, asyncio, logging, sqlite3, getpass
from discord.ext import commands
from dotenv import load_dotenv
load_dotenv(); TOKEN = os.getenv('TOKEN')

# Set up logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s'
)
logger = logging.getLogger()

intents = discord.Intents.all(); bot = commands.Bot(command_prefix="!", intents=intents); bot.remove_command('help')

def setup_database():
    conn = sqlite3.connect('.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Enable dictionary-style access
    return conn

# Store the database connection in the bot instance
bot.db = setup_database()

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
    
if __name__ == '__main__': 
    if TOKEN == "STRING":
        print("While providing your bot token, it will not be displayed but it is there...\nsimply copy it and paste it into the prompt below.\n")
        TOKEN = getpass.getpass("[ALERT] Please enter your Discord bot token: ")
        # Safely update only the TOKEN line in .env
        if os.path.exists('.env'):
            with open('.env', 'r') as f:
                lines = f.readlines()
            with open('.env', 'w') as f:
                for line in lines:
                    if line.strip().startswith('TOKEN'):
                        f.write(f'TOKEN = \'{TOKEN}\'\n')
                    else:
                        f.write(line)
        else:
            with open('.env', 'w') as f:
                f.write(f'TOKEN = \'{TOKEN}\'\n')
    bot.run(TOKEN)