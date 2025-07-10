import discord; from discord import app_commands; from discord.ext import commands, tasks
from typing import Dict, List, Set, Optional, Union, Any
from datetime import datetime, timedelta, timezone
import sqlite3, asyncio

class ThreadMonitor(commands.GroupCog, group_name="needle"):    
    """Cog for monitoring and managing thread activity with edit protection."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.THREAD_EMOJI = '🪡'  # Needle emoji for thread creation
        self._create_tables()
        # Cache for channel requirements with TTL
        self._channel_cache: Dict[int, Dict[str, Any]] = {}
        # Lock for thread-safe cache operations
        self._cache_lock = asyncio.Lock()
        
    def _create_tables(self) -> None:
        """Create necessary database tables for thread requirements."""
        cursor = self.bot.db.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS thread_requirements (
            channel_id INTEGER PRIMARY KEY,
            guild_id INTEGER NOT NULL,
            min_length INTEGER DEFAULT 0,
            required_keyword TEXT,
            auto_react BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
        )
        ''')
        self.bot.db.commit()
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle new messages in both channels and threads."""
        # Ignore messages from bots
        if message.author.bot:
            return
            
        # Handle messages in text channels (for thread creation reactions)
        if isinstance(message.channel, discord.TextChannel):
            try:
                # Use cached requirements if available
                requirements = await self.get_thread_requirements(message.channel.id, use_cache=True)
                if requirements and (requirements.get('auto_react', False) or any([requirements['min_length'], requirements['required_keyword']])):
                    # Check if message meets thread creation requirements
                    meets_requirements, _ = await self._check_thread_requirements(message.channel.id, message.content)
                    if meets_requirements and requirements.get('auto_react', False):
                        try:
                            # Add the thread reaction
                            await message.add_reaction(self.THREAD_EMOJI)
                        except discord.Forbidden:
                            pass
                        except Exception as e:
                            pass
            except Exception as e:
                pass
                # On error, try again without cache
                try:
                    requirements = await self.get_thread_requirements(message.channel.id, use_cache=False)
                    if requirements and requirements.get('auto_react', False):
                        await message.add_reaction(self.THREAD_EMOJI)
                except:
                    pass  # Give up if we still get an error
            
            # Don't process thread mode checks for channel messages
            return
    
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        """Handle reactions added to messages for thread creation."""
        # Ignore reactions from bots
        if payload.user_id == self.bot.user.id:
            return
            
        # Check if the reaction is our thread emoji
        if str(payload.emoji) != self.THREAD_EMOJI:
            return
            
        # Get the channel and message
        channel = self.bot.get_channel(payload.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
            
            # Check if the message meets thread requirements
            requirements = await self.get_thread_requirements(channel.id)
            if not requirements or not any([requirements['min_length'], requirements['required_keyword']]):
                return
                
            meets_requirements, _ = await self._check_thread_requirements(channel.id, message.content)
            
            if not meets_requirements:
                return
                
            # Remove the reaction to prevent multiple thread creations
            try:
                await message.remove_reaction(payload.emoji, payload.member or discord.Object(id=payload.user_id))
            except discord.Forbidden:
                return
                
            # Create a thread from the message using the helper method
            success, result = await self._create_thread_from_message(message)
            if success:
                # Remove the needle emoji
                await message.clear_reaction(self.THREAD_EMOJI)
                # Ping the user in the new thread and delete after 5 seconds
                if payload and payload.member:
                    try:
                        await result.send(f"{payload.member.mention} created this thread!\n-# Thread creation supported by the `/needle` cmds.")
                    except Exception:
                        pass
            else:
                pass
                
        except discord.NotFound:
            # Message was deleted or not found
            return
            
        except Exception as e:
            pass
    
    async def set_thread_requirements(
        self,
        channel: discord.TextChannel,
        min_length: Optional[int] = None,
        required_keyword: Optional[str] = None,
        auto_react: Optional[bool] = None
    ) -> None:
        """Set thread creation requirements for a channel and update cache."""
        cursor = self.bot.db.cursor()
        
        if min_length is None and required_keyword is None and auto_react is None:
            # Remove requirements if all are None
            cursor.execute(
                'DELETE FROM thread_requirements WHERE channel_id = ?',
                (channel.id,)
            )
            # Invalidate cache
            await self._update_cache(channel.id, None)
        else:
            # Update or insert requirements
            cursor.execute('''
                INSERT INTO thread_requirements (channel_id, guild_id, min_length, required_keyword, auto_react)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    min_length = COALESCE(?, min_length),
                    required_keyword = COALESCE(?, required_keyword),
                    auto_react = COALESCE(?, auto_react)
            ''', (
                channel.id,
                channel.guild.id,
                min_length,
                required_keyword.lower() if required_keyword else None,
                auto_react,
                min_length,
                required_keyword.lower() if required_keyword else None,
                auto_react
            ))
            # Update cache with new values
            requirements = await self.get_thread_requirements(channel.id, use_cache=False)
            await self._update_cache(channel.id, requirements)
        
        self.bot.db.commit()
    
    async def _get_cached_requirements(self, channel_id: int) -> Optional[dict]:
        """Get requirements from cache if valid, otherwise fetch from DB."""
        async with self._cache_lock:
            cache_entry = self._channel_cache.get(channel_id)
            if cache_entry and datetime.now() < cache_entry['expires_at']:
                return cache_entry['data']
            return None

    async def _update_cache(self, channel_id: int, data: Optional[dict]) -> None:
        """Update the cache with new data and set expiration."""
        async with self._cache_lock:
            if data is None:
                self._channel_cache.pop(channel_id, None)
            else:
                self._channel_cache[channel_id] = {
                    'data': data,
                    'expires_at': datetime.now() + timedelta(minutes=5)
                }

    async def get_thread_requirements(self, channel_id: int, use_cache: bool = True) -> Optional[dict]:
        """Get thread creation requirements for a channel with optional caching."""
        # Try to get from cache first if enabled
        if use_cache:
            cached = await self._get_cached_requirements(channel_id)
            if cached is not None:
                return cached

        # Not in cache or cache disabled, fetch from DB
        cursor = self.bot.db.cursor()
        cursor.execute(
            'SELECT min_length, required_keyword, auto_react FROM thread_requirements WHERE channel_id = ?',
            (channel_id,)
        )
        result = cursor.fetchone()
        
        requirements = None
        if result:
            requirements = {
                'min_length': result[0],
                'required_keyword': result[1],
                'auto_react': bool(result[2]) if result[2] is not None else False
            }
        
        # Update cache
        await self._update_cache(channel_id, requirements)
        self.bot.db.commit()
        return requirements
    
    @app_commands.command(name="setup", description="Setup thread requirements for a channel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        min_length="Minimum message length to create a thread",
        required_keyword="Required keyword in message to create a thread",
        auto_react="Whether to automatically add thread reaction to qualifying messages",
        channel="Channel to apply rules to (defaults to current channel)"
    )
    async def set_thread_rules(
        self,
        interaction: discord.Interaction,
        min_length: Optional[int] = None,
        required_keyword: Optional[str] = None,
        auto_react: Optional[bool] = None,
        channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Set thread creation rules for a channel."""
        target_channel = channel or interaction.channel
        
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
            return
            
        # Get current requirements to handle partial updates
        current = await self.get_thread_requirements(target_channel.id)
        
        # If no current requirements and no new ones, nothing to do
        if not current and min_length is None and required_keyword is None and auto_react is None:
            await interaction.response.send_message(
                "ℹ️ No thread creation rules were set or updated.",
                ephemeral=True
            )
            return
            
        # Prepare the new values
        new_min_length = None
        new_required_keyword = None
        
        # Only update values that were explicitly provided
        if min_length is not None:
            new_min_length = min_length if min_length > 0 else None
        
        if required_keyword is not None:
            new_required_keyword = required_keyword if required_keyword.strip() else None
        
        # If all parameters were explicitly set to None/empty, remove all requirements
        if (min_length is not None and required_keyword is not None and auto_react is None and 
            new_min_length is None and new_required_keyword is None):
            await self.set_thread_requirements(target_channel, None, None, None)
            await interaction.response.send_message(
                f"✅ Removed all thread creation rules from {target_channel.mention}",
                ephemeral=True
            )
            return
            
        # Set the requirements with the new values
        await self.set_thread_requirements(
            target_channel, 
            new_min_length, 
            new_required_keyword,
            auto_react
        )
        
        # Build response message
        rules = []
        if new_min_length is not None:
            rules.append(f"• Minimum length: {new_min_length} characters")
        if new_required_keyword is not None:
            rules.append(f"• Required keyword: \"{new_required_keyword}\"")
        if auto_react is not None:
            rules.append(f"• Auto-react: {'Enabled' if auto_react else 'Disabled'}")
            
        if rules:
            await interaction.response.send_message(
                f"✅ Updated thread creation rules for {target_channel.mention}:\n" + "\n".join(rules),
                ephemeral=True
            )
        else:
            await self.set_thread_requirements(target_channel, None, None, None)
            await interaction.response.send_message(
                f"✅ Removed all thread creation rules from {target_channel.mention}",
                ephemeral=True
            )
    @app_commands.command(name="view", description="View thread requirements for a channel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="Channel to check rules for (defaults to current channel)"
    )
    async def show_thread_rules(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Show thread creation rules for this channel."""
        target_channel = channel or interaction.channel
        
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
            return
            
        requirements = await self.get_thread_requirements(target_channel.id)
        
        if not requirements or (not requirements.get('min_length') and 
                              not requirements.get('required_keyword') and 
                              not requirements.get('auto_react')):
            await interaction.response.send_message(
                f"ℹ️ No thread creation rules set for {target_channel.mention}.",
                ephemeral=True
            )
            return
            
        rules = []
        if requirements.get('min_length'):
            rules.append(f"• Minimum length: {requirements['min_length']} characters")
        if requirements.get('required_keyword'):
            rules.append(f"• Required keyword: \"{requirements['required_keyword']}\"")
        if requirements.get('auto_react') is not None:
            status = "Enabled" if requirements['auto_react'] else "Disabled"
            rules.append(f"• Auto-react: {status}")
            
        await interaction.response.send_message(
            f"📜 Thread creation rules for {target_channel.mention}:\n" + "\n".join(rules),
            ephemeral=True
        )
    
    @app_commands.command(name="delete", description="Delete thread creation rules for a channel.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="Channel to delete thread creation rules for (defaults to current channel)",
        min_length="Set to True to delete the minimum length requirement",
        keyword="Set to True to delete the keyword requirement"
    )
    async def delete_thread_rules(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        min_length: Optional[bool] = False,
        keyword: Optional[bool] = False
    ) -> None:
        """
        Delete thread creation rules for this channel.
        
        If no specific rule is specified, all rules will be deleted.
        """
        target_channel = channel or interaction.channel
        
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
            return
        
        cursor = self.bot.db.cursor()
        
        if min_length or keyword:
            # Update only specified fields to NULL
            updates = []
            params = []
            
            if min_length:
                updates.append("min_length = NULL")
            if keyword:
                updates.append("required_keyword = NULL")
            
            # If all fields would be NULL, delete the entire row
            cursor.execute(
                'SELECT min_length, required_keyword FROM thread_requirements WHERE channel_id = ?',
                (target_channel.id,)
            )
            current = cursor.fetchone()
            
            if current:
                current_min, current_keyword = current
                will_be_null = (
                    (min_length or current_min is None) and
                    (keyword or current_keyword is None)
                )
                
                if will_be_null:
                    # Delete the entire row if all fields would be NULL
                    cursor.execute(
                        'DELETE FROM thread_requirements WHERE channel_id = ?',
                        (target_channel.id,)
                    )
                else:
                    # Update only the specified fields
                    set_clause = ", ".join(updates)
                    query = f"""
                        UPDATE thread_requirements 
                        SET {set_clause}
                        WHERE channel_id = ?
                    """
                    cursor.execute(query, (target_channel.id,))
        else:
            # Delete all rules if no specific ones specified
            cursor.execute(
                'DELETE FROM thread_requirements WHERE channel_id = ?',
                (target_channel.id,)
            )
        
        self.bot.db.commit()
        
        # Invalidate cache
        await self._update_cache(target_channel.id, None)
        
        # Create response message
        if min_length or keyword:
            deleted_rules = []
            if min_length:
                deleted_rules.append("minimum length requirement")
            if keyword:
                deleted_rules.append("keyword requirement")
            message = f"✅ Deleted {' and '.join(deleted_rules)} for {target_channel.mention}"
        else:
            message = f"✅ Deleted all thread creation rules for {target_channel.mention}"
            
        await interaction.response.send_message(message, ephemeral=True)
    
    async def _check_thread_requirements(self, channel_id: int, message_content: str) -> tuple[bool, Optional[str]]:
        """Check if a message meets the thread creation requirements."""
        requirements = await self.get_thread_requirements(channel_id)
        if not requirements or (not requirements['min_length'] and not requirements['required_keyword']):
            return False, "❌ No thread creation rules are set for this channel."
            
        if requirements['min_length'] and len(message_content) < requirements['min_length']:
            return False, f"❌ Message must be at least `{requirements['min_length']}` characters to create a thread."
            
        if requirements['required_keyword'] and requirements['required_keyword'].lower() not in message_content.lower():
            return False, f"❌ Message must contain the keyword \"{requirements['required_keyword']}\" to create a thread."
            
        return True, None
        
    async def _create_thread_from_message(self, message: discord.Message) -> tuple[bool, Union[discord.Thread, str, None]]:
        """Helper method to create a thread from a message.
        
        Returns:
            tuple[bool, Union[discord.Thread, str, None]]: 
                - First element is success status (bool)
                - Second element is the thread object on success, error message on failure, or None if thread exists
        """
        # Check if a thread already exists for this message
        if hasattr(message, 'thread') and message.thread is not None:
            return False, None  # Silently return if thread already exists
            
        try:
            content = message.content.strip() or "New thread"
            
            # Truncate to 96 characters to leave room for "..." if needed
            if len(content) > 96:
                truncated = content[:96]
                last_space = truncated.rfind(' ')
                if last_space > 0:  # If there's a space, truncate there
                    thread_name = content[:last_space] + "..."
                else:  # No spaces found, hard truncate
                    thread_name = truncated + "..."
            else:
                thread_name = content
                
            thread = await message.create_thread(name=thread_name)
            return True, thread  # Return the thread object on success
        except Exception as e:
            if "already has a thread" in str(e).lower():
                return False, None  # Silently return if thread already exists
            return False, f"❌ Failed to create thread: {str(e)}"

# Context menu command must be defined outside the class
@app_commands.context_menu(name="Create Thread from Message")
async def create_thread_from_message(interaction: discord.Interaction, message: discord.Message) -> None:
    """Create a thread from a message, checking requirements."""
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("❌ This command can only be used in text channels.", ephemeral=True)
        return
        
    # Get the cog instance
    cog = interaction.client.get_cog('ThreadMonitor')
    if not cog:
        await interaction.response.send_message("❌ ThreadMonitor cog is not loaded.", ephemeral=True)
        return
    
    # Check requirements
    valid, error = await cog._check_thread_requirements(interaction.channel.id, message.content)
    if not valid:
        await interaction.response.send_message(error, ephemeral=True)
        return
    
    # Create the thread
    success, result = await cog._create_thread_from_message(message)
    if success and result:  # If thread was created successfully
        thread = result  # result is the thread object
        # Send ephemeral response first
        await interaction.response.send_message(f"✅ Created thread: {thread.mention}", ephemeral=True)
        # Ping the user in the new thread and delete after 5 seconds
        try:
            await thread.send(f"{interaction.user.mention} created this thread!\n-# Thread creation supported by the `/needle` cmds.")
        except Exception as e:
            print(f"Error sending ping message: {e}")
    elif result is not None:  # If there was an error (result is error message string)
        await interaction.response.send_message(result, ephemeral=True)
    # If result is None, it means a thread already exists - don't send any message

async def setup(bot: commands.Bot) -> None:
    """Load the ThreadMonitor cog."""
    await bot.add_cog(ThreadMonitor(bot))
    bot.tree.add_command(create_thread_from_message)
