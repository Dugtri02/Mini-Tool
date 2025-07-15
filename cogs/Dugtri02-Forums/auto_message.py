import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio

class AutoMessage(commands.GroupCog, name="automsg"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._create_table()
    
    def _create_table(self):
        """Create the forum_auto_messages table if it doesn't exist."""
        try:
            cursor = self.db.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS forum_auto_messages (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    message_content TEXT NOT NULL
                )
            ''')
            self.db.commit()
            cursor.close()
        except Exception as e:
            print(f"Error creating forum_auto_messages table: {e}")
    
    async def _get_forum_auto_message(self, guild_id: int) -> Optional[tuple]:
        """Get the auto-message configuration for a guild."""
        try:
            cursor = self.db.cursor()
            cursor.execute(
                'SELECT channel_id, message_content FROM forum_auto_messages WHERE guild_id = ?',
                (guild_id,)
            )
            result = cursor.fetchone()
            cursor.close()
            return result
        except Exception as e:
            print(f"Error getting forum auto-message: {e}")
            return None
    
    async def _set_forum_auto_message(self, guild_id: int, channel_id: int, message_content: str) -> bool:
        """Set or update the auto-message configuration for a guild."""
        try:
            cursor = self.db.cursor()
            cursor.execute('''
                INSERT INTO forum_auto_messages (guild_id, channel_id, message_content)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    message_content = excluded.message_content
            ''', (guild_id, channel_id, message_content))
            self.db.commit()
            cursor.close()
            return True
        except Exception as e:
            print(f"Error setting forum auto-message: {e}")
            return False

    async def _format_auto_message(self, message: str, thread: discord.Thread) -> str:
        """Format the auto-message with thread information."""
        formatted_message = message
        
        # Safely replace thread placeholders with fallbacks
        try:
            formatted_message = formatted_message.replace("{thread}", thread.name)
        except (AttributeError, TypeError):
            formatted_message = formatted_message.replace("{thread}", "**#thread**")
            
        try:
            formatted_message = formatted_message.replace("{user}", thread.owner.mention)
        except (AttributeError, TypeError):
            formatted_message = formatted_message.replace("{user}", "**@user**")
            
        try:
            formatted_message = formatted_message.replace("{guild}", thread.guild.name)
        except (AttributeError, TypeError):
            formatted_message = formatted_message.replace("{guild}", "**#guild**")
            
        try:
            formatted_message = formatted_message.replace("{channel}", thread.parent.name if thread.parent else "**#channel**")
        except (AttributeError, TypeError):
            formatted_message = formatted_message.replace("{channel}", "**#channel**")
        
        # Remove {reply} placeholder if it's in the message
        formatted_message = formatted_message.replace("{reply}", "")
        
        # Handle newline placeholders
        formatted_message = formatted_message.replace("\\n", "\n")
        formatted_message = formatted_message.replace("{ln}", "\n")
        formatted_message = formatted_message.replace("{line}", "\n")
        
        return formatted_message
    
    @app_commands.command(name="set", description="Set an auto-message for new forum posts in a channel")
    @app_commands.describe(
        channel="The forum channel where the message should be posted",
        message="The message to send when a new forum post is created"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_forum_message(self, interaction: discord.Interaction, channel: discord.ForumChannel, message: str):
        """Set an auto-message for new forum posts in the specified channel."""
        if not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message("Please select a forum channel.", ephemeral=True)
            return
        
        success = await self._set_forum_auto_message(interaction.guild_id, channel.id, message)
        if success:
            formatted_message = await self._format_auto_message(message, channel)
            await interaction.response.send_message(
                f"✅ Auto-message set for forum channel {channel.mention}. "
                f"This message will be posted in all new forum posts.\n\n"
                "> Formatted Preview\n-# Please be aware some formatted text is not shown such as the {reply} placeholder\n"
                f"{formatted_message}",
                ephemeral=True
            )
        else:   
            await interaction.response.send_message(
                "❌ Failed to set auto-message. Please try again later.",
                ephemeral=True
            )

    @app_commands.command(name="view", description="View the auto-message configuration for a forum channel")
    @app_commands.describe(
        channel="The forum channel to view the auto-message for"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def get_forum_message(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        """View the auto-message configuration for a specific forum channel."""
        if not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message("Please select a forum channel.", ephemeral=True)
            return
            
        config = await self._get_forum_auto_message(interaction.guild_id)
        if config:
            channel_id, message = config
            if channel.id == channel_id:
                await interaction.response.send_message(
                    f"📝 **Auto-Message Configuration for {channel.mention}**\n"
                    f"**Message:**\n{message}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"ℹ️ No auto-message is configured for {channel.mention}.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "ℹ️ No auto-messages have been configured for any channels in this server yet.",
                ephemeral=True
            )

    @app_commands.command(name="remove", description="Remove the auto-message configuration for a forum channel")
    @app_commands.describe(
        channel="The forum channel to remove the auto-message from"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def clear_forum_message(self, interaction: discord.Interaction, channel: discord.ForumChannel):
        """Remove the auto-message configuration for a specific forum channel."""
        if not isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message("Please select a forum channel.", ephemeral=True)
            return
            
        try:
            # First check if there's a configuration for this channel
            config = await self._get_forum_auto_message(interaction.guild_id)
            if not config or config[0] != channel.id:
                await interaction.response.send_message(
                    f"ℹ️ No auto-message is configured for {channel.mention}.",
                    ephemeral=True
                )
                return
                
            cursor = self.db.cursor()
            cursor.execute(
                'DELETE FROM forum_auto_messages WHERE guild_id = ?',
                (interaction.guild_id,)
            )
            self.db.commit()
            cursor.close()
            await interaction.response.send_message(
                f"✅ Auto-message configuration for {channel.mention} has been removed.",
                ephemeral=True
            )
        except Exception as e:
            print(f"Error clearing forum auto-message: {e}")
            await interaction.response.send_message(
                "❌ Failed to remove auto-message configuration. Please try again later.",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        """Send the auto-message when a new forum post is created."""
        if not thread.parent or not isinstance(thread.parent, discord.ForumChannel):
            return
        
        config = await self._get_forum_auto_message(thread.guild.id)
        if not config:
            return
        
        channel_id, message = config
        if thread.parent.id != channel_id:
            return
        
        try:
            formatted_message = await self._format_auto_message(message, thread)
            if "{reply}" in message:
                await asyncio.sleep(2)
                try:
                    starter_message = await thread.fetch_message(thread.starter_message.id)
                    await starter_message.reply(formatted_message)
                except:
                    await thread.send(formatted_message)
            else:
                await thread.send(formatted_message)
                
        except Exception as e:
            print(f"Error sending auto-message to thread {thread.id}: {e}")

async def setup(bot):
    await bot.add_cog(AutoMessage(bot))
