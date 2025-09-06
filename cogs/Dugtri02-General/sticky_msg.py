import discord
import time
from discord.ext import commands
from discord import app_commands
import asyncio

class StickyMsg(commands.GroupCog, name="stickymsg"):
    # List of guild IDs that can have unlimited sticky messages
    UNLIMITED_STICKY_GUILDS = [
        271776624490446858,  # Mole Co.
        967568726935339039   # Bismuth
    ]
    MAX_STICKY_MESSAGES = 2  # Maximum sticky messages for regular guilds

    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.last_sticky_messages = {}  # Store last sticky message IDs by channel ID
        self.pending_stickies = {}  # Track pending sticky updates by channel ID
        self.create_tables()
    
    def create_tables(self):
        c = self.db.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS sticky_msg (
            guild_id INTEGER,
            channel_id INTEGER,
            message_content TEXT,
            PRIMARY KEY (guild_id, channel_id)
        )
        """)
        self.db.commit()
        

    def get_guild_sticky_count(self, guild_id: int) -> int:
        """Get the current number of sticky messages for a guild"""
        c = self.db.cursor()
        c.execute("""
        SELECT COUNT(*) FROM sticky_msg 
        WHERE guild_id = ?
        """, (guild_id,))
        return c.fetchone()[0] or 0

    @app_commands.command(name="set", description="Set a sticky message")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_sticky_msg(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str):
        await interaction.response.defer(ephemeral=True)
        try:
            guild_id = interaction.guild.id
            
            # Check if this is a new sticky message (not an update to existing one)
            c = self.db.cursor()
            c.execute("""
            SELECT COUNT(*) FROM sticky_msg 
            WHERE guild_id = ? AND channel_id = ?
            """, (guild_id, channel.id))
            is_update = c.fetchone()[0] > 0
            
            # Check message limit if not an update and guild is not in unlimited list
            if not is_update and guild_id not in self.UNLIMITED_STICKY_GUILDS:
                current_count = self.get_guild_sticky_count(guild_id)
                if current_count >= self.MAX_STICKY_MESSAGES:
                    return await interaction.followup.send(
                        f"You've reached the maximum of {self.MAX_STICKY_MESSAGES} sticky messages for this server. "
                        "Please remove an existing sticky message before adding a new one."
                    )
            
            # Use INSERT OR REPLACE to handle updates to existing sticky messages
            c.execute("""
            INSERT OR REPLACE INTO sticky_msg (guild_id, channel_id, message_content)
            VALUES (?, ?, ?)
            """, (guild_id, channel.id, message))
            self.db.commit()
            
            await interaction.followup.send("Sticky message set successfully.")
        except Exception as e:
            await interaction.followup.send(f"Error setting sticky message: {e}")

    @app_commands.command(name="remove", description="Remove a sticky message")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_sticky_msg(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            c = self.db.cursor()
            c.execute("""
            DELETE FROM sticky_msg
            WHERE guild_id = ? AND channel_id = ?
            """, (interaction.guild.id, channel.id))
            self.db.commit()
            await interaction.followup.send("Sticky message removed successfully.")
        except Exception as e:
            await interaction.followup.send(f"Error removing sticky message: {e}")

    @app_commands.command(name="view", description="View the sticky message of a specified channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def view_sticky_msg(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        try:
            c = self.db.cursor()
            c.execute("""
            SELECT message_content FROM sticky_msg
            WHERE guild_id = ? AND channel_id = ?
            """, (interaction.guild.id, channel.id))
            result = c.fetchone()
            if result:
                await interaction.followup.send(result[0])
            else:
                await interaction.followup.send("No sticky message found for this channel.")
        except Exception as e:
            await interaction.followup.send(f"Error viewing sticky message: {e}")

    @app_commands.command(name="clear", description="Clear all sticky messages")
    @app_commands.checks.has_permissions(administrator=True)
    async def clear_sticky_msg(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            c = self.db.cursor()
            c.execute("DELETE FROM sticky_msg WHERE guild_id = ?", (interaction.guild.id,))
            self.db.commit()
            await interaction.followup.send("All sticky messages cleared successfully.")
        except Exception as e:
            await interaction.followup.send(f"Error clearing sticky messages: {e}")

    async def process_sticky_update(self, channel_id, guild_id, channel):
        # Store the task reference before any awaits
        task = asyncio.current_task()
        
        try:
            # Wait 5 seconds initially
            await asyncio.sleep(5)
            
            # Keep extending the wait if new messages come in for this specific channel
            while channel_id in self.pending_stickies and self.pending_stickies[channel_id].get('task') is task:
                remaining = self.pending_stickies[channel_id]['expiry'] - time.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(0.1, remaining))  # Check more frequently but sleep in small increments
            
            # If we were cancelled by a new message, exit
            if channel_id not in self.pending_stickies:
                return
                
        except asyncio.CancelledError:
            # Task was cancelled, exit cleanly
            return
            
        # Get the latest sticky message content
        c = self.db.cursor()
        c.execute("SELECT * FROM sticky_msg WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id))
        result = c.fetchone()
        if not result:
            return
            
        # Delete previous sticky message if exists
        message_deleted = False
        if channel_id in self.last_sticky_messages:
            try:
                last_message = await channel.fetch_message(self.last_sticky_messages[channel_id])
                await last_message.delete()
                message_deleted = True
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        
        # If we couldn't delete from cache, search last 100 messages for sticky message
        if not message_deleted:
            try:
                found = False
                # Get the original content from database for comparison
                db_content = result[2]
                # Get the formatted version (with @silent removed)
                formatted_content, _ = await self.format_message(db_content)
                
                # Create a set of possible message variants to match
                possible_contents = {
                    db_content,  # Original content with @silent
                    formatted_content,  # Formatted content without @silent
                    db_content.replace('@silent', '').strip(),  # @silent removed but not formatted
                    db_content.replace('`', '')  # No code blocks
                }
                
                # If the message had @silent, also check the version with @silent at start
                if db_content.startswith('@silent'):
                    possible_contents.add(db_content)
                    possible_contents.add('@silent ' + formatted_content)
                
                async for old_message in channel.history(limit=100):
                    if old_message.author == channel.guild.me:
                        current_content = old_message.content.replace('\r\n', '\n').strip()
                        
                        # Check if this message matches any of our possible contents
                        if current_content in possible_contents or \
                           any(c.replace('\r\n', '\n').strip() == current_content for c in possible_contents):
                            try:
                                await old_message.delete()
                                found = True
                                break
                            except Exception:
                                continue
            except (discord.Forbidden, discord.HTTPException):
                pass
        
        # # Wait 0.2 seconds before sending the new message
        # await asyncio.sleep(0.2)

        # Format message and check for @silent tag
        message_content, silent = await self.format_message(result[2])
        
        # Send the message with silent notification if needed
        new_message = await channel.send(
            content=message_content,
            silent=silent
        )
        self.last_sticky_messages[channel_id] = new_message.id
        
        # Clean up the pending sticky if this task is still the current one
        if channel_id in self.pending_stickies and self.pending_stickies[channel_id].get('task') is asyncio.current_task():
            del self.pending_stickies[channel_id]

    async def format_message(self, message_content):
        """Helper method to format message content and check for silent flag"""
        silent = False
        if message_content.startswith('@silent'):
            message_content = message_content.replace('@silent', '', 1).strip()
            silent = True
        message_content = message_content.replace('\\n', '\n')
        
        return message_content, silent
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        
        c = self.db.cursor()
        c.execute("SELECT * FROM sticky_msg WHERE guild_id = ? AND channel_id = ?", 
                 (message.guild.id, message.channel.id))
        result = c.fetchone()
        
        if result:
            channel_id = message.channel.id
            
            # Cancel any existing task for this channel
            if channel_id in self.pending_stickies and self.pending_stickies[channel_id].get('task'):
                try:
                    self.pending_stickies[channel_id]['task'].cancel()
                    # Wait a moment for the task to be cancelled
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
            
            # Create or update the pending sticky with a new expiry time (current time + 5 seconds)
            task = self.bot.loop.create_task(
                self.process_sticky_update(channel_id, message.guild.id, message.channel)
            )
            
            self.pending_stickies[channel_id] = {
                'expiry': time.time() + 5,
                'task': task
            }

async def setup(bot):
    await bot.add_cog(StickyMsg(bot))