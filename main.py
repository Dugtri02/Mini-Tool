import discord, os, asyncio, logging, sqlite3, getpass, random
from discord.ext import commands, tasks
from dotenv import load_dotenv
load_dotenv(); TOKEN = os.getenv('TOKEN')

# Set up logger
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s:%(name)s: %(message)s'
)
logger = logging.getLogger()

intents = discord.Intents.default()
# intents.members = True # Uncomment if the cogs you use require seeing members and their info
# intents.message_content = True # Uncomment if the cogs you use require seeing messages and their contents
# intents.presences = True # Uncomment if the cogs you use require seeing presences

# Create the bot, do NOT change to autosharded, this is built on "Sqlite" which struggles with sharding
bot = commands.Bot(command_prefix="!", intents=intents); bot.remove_command('help') # Remove the default help command so we can make our own

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

    # Start the status change loop
    change_status.start()

@tasks.loop(hours=1, reconnect=True)
async def change_status():
    server_count = len(bot.guilds)
    
    possible_statuses = [ # Invite link goes to the Mini-Tool support server
        f"Watchin' {server_count} Guilds",
        f"https://discord.gg/Dt8jxXsXwe"#,
        #f"example status 1",
        #f"example status 2, always add a comma after the last status quote"
    ]
    
    # Choose a random status from the list
    chosen_status = random.choice(possible_statuses)
    
    # Create the custom activity
    act = discord.CustomActivity(name=chosen_status)
    
    # Set the presence
    await bot.change_presence(status=discord.Status.idle, activity=act)

@bot.event
async def on_guild_remove(guild):
        """Clean up guild data when the bot is removed from a guild."""
        try: 
            # Clean up the guild's data
            result = await clean_guild_data(bot.db, guild.id)
            
            if result['success']:
                pass
            else:
                print(f"Failed to clean up data for guild {guild.name} ({guild.id}): {result['error']}")
                
        except Exception as e:
            print(f"Error in on_guild_remove for guild {guild.id}: {e}")

async def clean_guild_data(db, guild_id: int) -> dict:
    """
    Remove all entries for a specific guild from all database tables.
    Returns a dictionary with the results of the cleanup operation.
    """
    try:
        c = db.cursor()
        
        # Get all table names that might contain guild data, excluding the bans table
        c.execute("""
            SELECT name 
            FROM sqlite_master 
            WHERE type='table' 
            AND name != 'bans'  -- Explicitly exclude the bans table
        """)
        tables = [row[0] for row in c.fetchall()]
        
        # Track affected rows
        total_deleted = 0
        results = {}
        
        for table in tables:
            try:
                # Get column names for the table
                c.execute(f"PRAGMA table_info({table})")
                columns = [col[1] for col in c.fetchall()]
                
                # Check if table has a guild_id column
                if 'guild_id' in columns:
                    # Delete rows for the specified guild
                    c.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
                    affected = c.rowcount
                    if affected > 0:  # Only count if rows were actually deleted
                        total_deleted += affected
                        results[table] = affected
                
                if 'guild_one_id' in columns:
                    # Delete rows for the specified guild
                    c.execute(f"DELETE FROM {table} WHERE guild_one_id = ?", (guild_id,))
                    affected = c.rowcount
                    if affected > 0:  # Only count if rows were actually deleted
                        total_deleted += affected
                        results[table] = affected
                
                if 'guild_two_id' in columns:
                    # Delete rows for the specified guild
                    c.execute(f"DELETE FROM {table} WHERE guild_two_id = ?", (guild_id,))
                    affected = c.rowcount
                    if affected > 0:  # Only count if rows were actually deleted
                        total_deleted += affected
                        results[table] = affected
                    
            except sqlite3.Error as e:
                results[f"error_{table}"] = str(e)
        
        # Commit changes
        db.commit()
        
        return {
            'success': True,
            'guild_id': guild_id,
            'tables_affected': results,
            'total_deleted': total_deleted
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
    
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