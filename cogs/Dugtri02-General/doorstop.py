import discord
from discord.ext import commands
from discord import app_commands

class DoorstopCog(commands.GroupCog, name="doorstop"):
    def __init__(self, bot: commands.AutoShardedBot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    def _create_tables(self):
        cursor = self.db.cursor()
        # If table does not exist, create it
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS doorstop_threads (
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            thread_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, thread_id)
        )
        ''')
        self.db.commit()


    @app_commands.command(name="add", description="Add a thread to the doorstop list")
    @app_commands.checks.has_permissions(manage_threads=True)
    @app_commands.describe(
        thread="The thread to add to the doorstop list",
    )
    async def add(self, interaction: discord.Interaction, thread: discord.Thread):
        try:
            # Get the parent channel
            parent_channel = thread.parent
            if not parent_channel:
                await interaction.response.send_message("Could not determine the parent channel of this thread.", ephemeral=True)
                return
            
            cursor = self.db.cursor()
            
            # Check if this thread is already in the doorstop list
            cursor.execute('''
            SELECT thread_id FROM doorstop_threads 
            WHERE guild_id = ? AND thread_id = ?
            ''', (interaction.guild.id, thread.id))
            
            if cursor.fetchone() is not None:
                await interaction.response.send_message(
                    "This thread is already in the doorstop list.",
                    ephemeral=True
                )
                return
            
            # Check current thread count for this guild
            cursor.execute('''
            SELECT COUNT(*) FROM doorstop_threads WHERE guild_id = ?
            ''', (interaction.guild.id,))
            thread_count = cursor.fetchone()[0]

            max_threads = 10
            
            if thread_count >= max_threads:
                await interaction.response.send_message(
                    f"Maximum of {max_threads} doorstop threads reached for this server. Please remove some before adding more.",
                    ephemeral=True
                )
                return
                
            cursor.execute('''
            INSERT OR REPLACE INTO doorstop_threads (guild_id, channel_id, thread_id)
            VALUES (?, ?, ?)
            ''', (interaction.guild.id, parent_channel.id, thread.id))
            self.db.commit()
            await interaction.response.send_message(
                f"Thread '{thread.mention}' in {parent_channel.mention} added to the doorstop list. "
                f"({thread_count + 1}/{max_threads} threads used)", 
                ephemeral=True
            )
        except Exception as e:
            print(f"Error adding thread: {e}")
            await interaction.response.send_message("Failed to add thread to the doorstop list.", ephemeral=True)

    @app_commands.command(name="remove", description="Remove a thread from the doorstop list.")
    @app_commands.checks.has_permissions(manage_threads=True)
    @app_commands.describe(
        thread="The thread to remove from the doorstop list",
    )
    async def remove(self, interaction: discord.Interaction, thread: discord.Thread):
        """Remove a thread from the doorstop list."""
        try:
                
            cursor = self.db.cursor()
            cursor.execute('''
            DELETE FROM doorstop_threads
            WHERE guild_id = ? AND thread_id = ?
            ''', (interaction.guild.id, thread.id))
            
            if cursor.rowcount > 0:
                self.db.commit()
                await interaction.response.send_message(
                    f"Thread '{thread.mention}' removed from the doorstop list.", 
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "This thread is not in the doorstop list.",
                    ephemeral=True
                )
                
        except Exception as e:
            print(f"Error removing thread: {e}")
            await interaction.response.send_message("Failed to remove thread from the doorstop list.", ephemeral=True)
    
    @app_commands.command(name="list", description="List all threads in the doorstop list.")
    @app_commands.checks.has_permissions(manage_threads=True)
    async def list(self, interaction: discord.Interaction):
        """List all threads in the doorstop list."""
        cursor = self.db.cursor()
        cursor.execute('''
        SELECT thread_id FROM doorstop_threads WHERE guild_id = ?
        ''', (interaction.guild.id,))
        thread_ids = [row[0] for row in cursor.fetchall()]
        
        # Check current thread count for this guild
        cursor.execute('''
        SELECT COUNT(*) FROM doorstop_threads WHERE guild_id = ?
        ''', (interaction.guild.id,))
        thread_count = cursor.fetchone()[0]
        
        if not thread_ids:
            await interaction.response.send_message("No threads found in the doorstop list.", ephemeral=True)
            return
        
        # Fetch all threads from all channels
        all_threads = []
        for channel in interaction.guild.text_channels:
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                try:
                    all_threads.extend(channel.threads)
                except Exception:
                    continue
        
        # Find threads that are in our list
        thread_objects = [t for t in all_threads if t.id in thread_ids]
        
        if not thread_objects:
            await interaction.response.send_message("No active threads found in the doorstop list.", ephemeral=True)
            return
        
        # Create an embed with the list of threads
        embed = discord.Embed(
            title="Doorstop List", 
            description="Threads that will be automatically reopened if closed:", 
            color=discord.Color.blue()
        )
        
        max_threads = 10
        
        embed.set_footer(text=f"{thread_count}/{max_threads} threads")
        
        for thread in thread_objects:
            channel_name = f" in {thread.parent.mention}" if thread.parent else ""
            embed.add_field(
                name=thread.name,
                value=f"<#{thread.id}>{channel_name}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @app_commands.command(name="clear", description="Clear all threads from the doorstop list.")
    @app_commands.checks.has_permissions(manage_threads=True)
    async def clear(self, interaction: discord.Interaction):
        """Clear all threads from the doorstop list."""
        cursor = self.db.cursor()
        cursor.execute('''
        DELETE FROM doorstop_threads WHERE guild_id = ?
        ''', (interaction.guild.id,))
        self.db.commit()
        await interaction.response.send_message("All threads removed from the doorstop list.", ephemeral=True)
    
    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        """Reopen threads in the doorstop list if they're closed."""
        # Check if the thread was just closed
        if before.archived == after.archived:
            return  # No change in archived state
            
        if after.archived:  # Thread was just archived/closed
            cursor = self.db.cursor()
            cursor.execute('''
            SELECT 1 FROM doorstop_threads 
            WHERE guild_id = ? AND thread_id = ?
            ''', (after.guild.id, after.id))
            
            if cursor.fetchone():  # Thread is in the doorstop list
                try:
                    await after.edit(archived=False, reason="Opened by '/doorstop' configuration.")
                except discord.HTTPException as e:
                    print(f"Failed to reopen thread {after.id}: {e}")

async def setup(bot):
    await bot.add_cog(DoorstopCog(bot))
