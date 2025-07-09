import discord
import time
import asyncio
from discord.ext import commands, tasks
from discord import app_commands
from typing import Dict, List, Optional, Any, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

# Cache structure to store thread data
@dataclass
class ThreadMemberCache:
    member_ids: Set[int] = field(default_factory=set)
    last_updated: float = field(default_factory=time.time)

@dataclass
class ThreadCache:
    threads: List[discord.Thread]
    timestamp: float
    guild_id: int
    user_id: int
    channel_id: Optional[int] = None
    last_refresh: float = field(default_factory=time.time)

# View for pagination
class ThreadPaginationView(discord.ui.View):
    def __init__(self, cog, pages: List[discord.Embed], cache_key: str):
        super().__init__(timeout=600)  # 10 minute timeout
        self.cog = cog
        self.pages = pages
        self.current_page = 0
        self.cache_key = cache_key
        self.update_buttons()
    
    def update_buttons(self):
        # Update button states based on current page
        self.first_page.disabled = self.prev_page.disabled = self.current_page == 0
        self.last_page.disabled = self.next_page.disabled = self.current_page == len(self.pages) - 1
    
    async def update_embed(self, interaction: discord.Interaction):
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)
    
    @discord.ui.button(emoji='⏪', style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = 0
        await self.update_embed(interaction)
    
    @discord.ui.button(emoji='◀️', style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = max(0, self.current_page - 1)
        await self.update_embed(interaction)
    
    @discord.ui.button(emoji='▶️', style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self.update_embed(interaction)
    
    @discord.ui.button(emoji='⏩', style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = len(self.pages) - 1
        await self.update_embed(interaction)
    
    @discord.ui.button(emoji='🔄', style=discord.ButtonStyle.danger, label='Refresh')
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check cooldown
        current_time = time.time()
        if interaction.user.id in self.cog.last_refresh:
            time_since_refresh = current_time - self.cog.last_refresh[interaction.user.id]
            if time_since_refresh < 300:  # 5 minute cooldown in seconds
                remaining = 300 - int(time_since_refresh)
                cooldown_ends = int(current_time) + remaining
                await interaction.response.send_message(
                    f"You can refresh again <t:{cooldown_ends}:R>.",
                    ephemeral=True
                )
                return
        
        # Update last refresh time immediately to prevent rapid refreshes
        self.cog.last_refresh[interaction.user.id] = current_time
        
        # Acknowledge the interaction first
        await interaction.response.defer(ephemeral=True)
        
        # Invalidate the cache for this user's guild
        keys_to_remove = [
            key for key in self.cog.thread_cache.keys() 
            if key.startswith(f"{interaction.guild.id}:{interaction.user.id}")
        ]
        for key in keys_to_remove:
            if key in self.cog.thread_cache:
                del self.cog.thread_cache[key]
        
        # Clear member cache for this user's threads
        for thread_id in list(self.cog.member_cache.keys()):
            if interaction.user.id in self.cog.member_cache[thread_id].member_ids:
                del self.cog.member_cache[thread_id]
        
        # Send confirmation message
        await interaction.followup.send("Thread list has been refreshed. Please use the command again to see the updated list.", ephemeral=True)

class Compass_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.thread_cache: Dict[str, ThreadCache] = {}
        self.member_cache: Dict[int, ThreadMemberCache] = defaultdict(ThreadMemberCache)
        self.last_refresh: Dict[int, float] = {}  # user_id -> last_refresh_time
        self.cleanup_old_cache.start()
    
    def cog_unload(self):
        self.cleanup_old_cache.cancel()
    
    @tasks.loop(minutes=5, reconnect=True)
    async def cleanup_old_cache(self):
        """Clean up old cache entries"""
        current_time = time.time()
        
        # Clean thread cache (older than 4 hours)
        to_remove = [
            key for key, cache in self.thread_cache.items()
            if current_time - cache.timestamp > 14400  # 4 hours
        ]
        for key in to_remove:
            del self.thread_cache[key]
        
        # Clean member cache (older than 4 hours)
        to_remove_members = [
            thread_id for thread_id, cache in self.member_cache.items()
            if current_time - cache.last_updated > 14400  # 4 hours
        ]
        for thread_id in to_remove_members:
            del self.member_cache[thread_id]
            
        # Clean old refresh times (older than 30 seconds)
        self.last_refresh = {
            user_id: timestamp 
            for user_id, timestamp in self.last_refresh.items()
            if current_time - timestamp < 30  # 30 second cooldown
        }
    
    def get_cache_key(self, guild_id: int, user_id: int, channel_id: Optional[int] = None) -> str:
        """Generate a unique cache key"""
        return f"{guild_id}:{user_id}:{channel_id if channel_id else 'all'}"
    
    def escape_thread_name(self, name: str) -> str:
        """Escape special characters in thread names to prevent formatting issues"""
        # List of Discord markdown special characters that need to be escaped
        special_chars = ['*', '_', '~', '`', '|', '>', ':', '-', '=', '#']
        
        # Escape each special character with a backslash
        for char in special_chars:
            name = name.replace(char, f'\\{char}')
            
        return name
    
    async def get_thread_members(self, thread: discord.Thread) -> Set[int]:
        """Get thread members with caching and rate limiting"""
        current_time = time.time()
        cache = self.member_cache.get(thread.id)
        
        # Return cached members if recent (last 5 minutes)
        if cache and current_time - cache.last_updated < 300:
            return cache.member_ids
            
        # Add small delay before API call
        await asyncio.sleep(0.5)  # 500ms delay between member fetches
            
        # Fetch fresh members
        try:
            members = await thread.fetch_members()
            member_ids = {member.id for member in members}
            self.member_cache[thread.id] = ThreadMemberCache(
                member_ids=member_ids,
                last_updated=current_time
            )
            return member_ids
        except Exception as e:
            print(f"Error fetching members for thread {thread.name}: {e}")
            return set()
    
    async def get_user_threads(self, guild: discord.Guild, user: discord.Member, 
                            channel: Optional[discord.TextChannel] = None,
                            force_refresh: bool = False) -> List[discord.Thread]:
        """Get all threads the user is a member of, with optional channel filter"""
        cache_key = self.get_cache_key(guild.id, user.id, channel.id if channel else None)
        current_time = time.time()
        
        # Check refresh cooldown (30 seconds per user)
        if user.id in self.last_refresh:
            time_since_refresh = current_time - self.last_refresh[user.id]
            if time_since_refresh < 30:  # 30 second cooldown
                force_refresh = False
        
        # Check cache first if not forcing refresh
        if not force_refresh and cache_key in self.thread_cache:
            cache = self.thread_cache[cache_key]
            if current_time - cache.timestamp < 14400:  # 4 hours
                return cache.threads
        
        # If cache miss, expired, or forcing refresh, fetch fresh data
        threads = []
        channels = [channel] if channel else [c for c in guild.channels if isinstance(c, discord.TextChannel)]
        
        for ch in channels:
            try:
                # Get active threads (including private ones the bot can see)
                for i, thread in enumerate(ch.threads, 1):
                    if (thread.permissions_for(guild.me).manage_threads or 
                        not thread.is_private() or 
                        guild.me in thread.members):
                        threads.append(thread)
                    # Add small delay every 5 threads
                    if i % 5 == 0:
                        await asyncio.sleep(0.3)  # 300ms delay every 5 threads
                
                # Get archived threads (including private ones the bot can see)
                archived_count = 0
                async for thread in ch.archived_threads(limit=None):
                    if (thread.permissions_for(guild.me).manage_threads or 
                        not thread.is_private() or 
                        guild.me in thread.members):
                        threads.append(thread)
                    # Add small delay every 5 archived threads
                    archived_count += 1
                    if archived_count % 5 == 0:
                        await asyncio.sleep(0.3)  # 300ms delay every 5 archived threads
            except Exception as e:
                print(f"Error fetching threads from {ch.name}: {e}")
                continue
        
        # Filter threads where the user is a member using cached data when possible
        user_threads = []
        for i, thread in enumerate(threads, 1):
            try:
                member_ids = await self.get_thread_members(thread)
                if user.id in member_ids:
                    user_threads.append(thread)
                # Add small delay every 3 thread member checks
                if i % 3 == 0:
                    await asyncio.sleep(0.2)  # 200ms delay every 3 checks
            except Exception as e:
                print(f"Error checking thread {thread.name}: {e}")
                continue
        
        # Sort by last message time (newest first)
        user_threads.sort(key=lambda t: getattr(t, 'last_message_id', 0) or 0, reverse=True)
        
        # Update cache with new data
        self.thread_cache[cache_key] = ThreadCache(
            threads=user_threads,
            timestamp=current_time,
            guild_id=guild.id,
            user_id=user.id,
            channel_id=channel.id if channel else None,
            last_refresh=current_time
        )
        
        # Update last refresh time for the user
        self.last_refresh[user.id] = current_time
        
        return user_threads
    
    @app_commands.command(name="compass", description="Shows threads you're part of in this server")
    @app_commands.describe(
        channel="Filter by specific channel (optional)",
        page="Page number to view (starts at 1)",
        thread_type="Filter by thread type (public, private, or all)"
    )
    @app_commands.choices(thread_type=[
        app_commands.Choice(name="All Threads", value="all"),
        app_commands.Choice(name="Public Threads", value="public"),
        app_commands.Choice(name="Private Threads", value="private")
    ])
    async def my_threads(
        self, 
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        page: int = 1,
        thread_type: str = "all"
    ):
        """Shows threads you're part of in the current server"""
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get user's threads
            threads = await self.get_user_threads(
                interaction.guild, 
                interaction.user, 
                channel
            )
            
            if not threads:
                channel_msg = f" in {channel.mention}" if channel else ""
                await interaction.followup.send(f"You're not part of any threads{channel_msg}.", ephemeral=True)
                return
            
            # Filter by thread type if specified
            if thread_type != "all":
                is_private = thread_type == "private"
                threads = [t for t in threads if t.is_private() == is_private]
                if not threads:
                    type_msg = "private" if is_private else "public"
                    await interaction.followup.send(f"You're not part of any {type_msg} threads.", ephemeral=True)
                    return
            
            # Create a copy of threads to avoid modifying the original list
            threads_to_display = threads.copy()
            
            # Create paginated embeds (3 threads per page)
            chunks = [threads_to_display[i:i + 3] for i in range(0, len(threads_to_display), 3)]
            total_pages = len(chunks) or 1
            
            # Validate page number
            page = max(1, min(page, total_pages))
            
            # Generate all pages with consistent field structure
            all_pages = []
            
            for page_num, chunk in enumerate(chunks, 1):
                # Create a new embed for each page
                thread_type_text = f"{thread_type.capitalize() if thread_type != 'all' else ''} "
                page_embed = discord.Embed(
                    title=f"Your {thread_type_text if thread_type != 'all' else ''}Threads in {interaction.guild.name}"
                          f"{' (in #' + channel.name + ')' if channel else ''}",
                    description=f"Page {page_num}/{total_pages}",
                    color=discord.Color.dark_purple()
                )
                
                for thread in chunk:
                    # Get thread status - always check all statuses
                    status_indicators = []
                    if getattr(thread, 'archived', False):
                        status_indicators.append("⏱️ Closed")
                    if getattr(thread, 'locked', False):
                        status_indicators.append("🔒 Locked")
                    if getattr(thread, 'is_private', lambda: False)():
                        status_indicators.append("🔐 Private")
                
                    # Get last message info if available
                    last_message = "No messages"
                    if thread.last_message_id:
                        try:
                            last_msg = await thread.fetch_message(thread.last_message_id)
                            last_message = f"<t:{int(last_msg.created_at.timestamp())}:R> by {last_msg.author.mention}"
                        except:
                            last_message = "Unknown time"
                    
                    # Build thread info with status indicators
                    thread_info = [
                        f"• Parent: {thread.parent.mention if thread.parent else 'Unknown'}",
                        f"• Created: <t:{int(thread.created_at.timestamp())}:D>",
                        f"• Last message: {last_message}",
                        f"• [Jump to Thread]({thread.jump_url})"
                    ]
                    
                    # Add status indicators if any
                    if status_indicators:
                        thread_info.insert(1, f"• Status: {'  '.join(status_indicators)}")
                    
                    # Add thread field to embed with escaped name
                    escaped_name = self.escape_thread_name(thread.name)
                    page_embed.add_field(
                        name=f"# {escaped_name}",
                        value="\n".join(thread_info) + f"\n{'='*30}",
                        inline=False
                    )
                
                all_pages.append(page_embed)
            
            # Create view with pagination controls
            cache_key = self.get_cache_key(interaction.guild.id, interaction.user.id, channel.id if channel else None)
            view = ThreadPaginationView(self, all_pages, cache_key)
            
            # Update the current page
            view.current_page = page - 1
            view.update_buttons()
            
            # Send the message with the first page and controls
            await interaction.followup.send(embed=all_pages[0], view=view, ephemeral=True)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            await interaction.followup.send(f"An error occurred while fetching your threads: {str(e)}", ephemeral=True)


# Setup function to add the cog to the bot
async def setup(bot):
    await bot.add_cog(Compass_cog(bot))