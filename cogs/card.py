import discord
import sqlite3
import time
import asyncio
import re
from datetime import datetime
from discord.ext import commands, tasks
from discord import app_commands
from typing import List, Dict, Optional, Any, Set, Tuple, Deque
from collections import defaultdict, deque
import logging

logger = logging.getLogger(__name__)

def transform_emoji(text: Optional[str]) -> Optional[str]:
    """Transform emojis in text to their Unicode equivalents.
    
    Args:
        text: The input text potentially containing emojis
        
    Returns:
        The transformed text with emojis converted to their Unicode equivalents
    """
    if not text:
        return text
        
    # This pattern matches emoji variation selectors (U+FE0F)
    return re.sub(r'\uFE0F', '', text)

class UpdateQueue:
    """Queue system for processing nickname updates with rate limiting."""
    def __init__(self, cog):
        self.cog = cog
        self.queue: Deque[Tuple[discord.Member, float]] = deque()
        self.processing = False
        self.last_update = 0
        self.rate_limit = 2.0  # 2 seconds between updates to respect rate limits
        self.batch_size = 5    # Process 5 members at a time with a delay
        
    async def add_member(self, member: discord.Member):
        """Add a member to the update queue."""
        # Skip if already in queue
        if not any(m.id == member.id for m, _ in self.queue):
            self.queue.append((member, time.time()))
            
        # Start processing if not already running
        if not self.processing:
            self.processing = True
            self.cog.bot.loop.create_task(self.process_queue())
    
    async def process_queue(self):
        """Process the queue with rate limiting."""
        while self.queue:
            now = time.time()
            
            # Process a batch of members
            processed = 0
            while self.queue and processed < self.batch_size:
                member, _ = self.queue.popleft()
                try:
                    await self.cog._process_member_update(member)
                    processed += 1
                except Exception as e:
                    print(f"Error processing member {member}: {e}")
            
            # Wait before processing next batch if queue not empty
            if self.queue:
                await asyncio.sleep(self.rate_limit)
        
        self.processing = False

class Card(commands.GroupCog, name="card"):
    def __init__(self, bot):
        self.bot = bot
        self._last_nick_update = {}  # Track last nickname update times for rate limiting
        self.db = bot.db
        # Cache: {guild_id: {'roles': {role_id: (prefix, suffix)}, 'expires': timestamp}}
        self.role_cache = {}
        self.cache_duration = 3600  # 1 hour in seconds
        self.update_queue = UpdateQueue(self)
        # Locks for guild operations to prevent concurrent modifications
        self.guild_locks = {}
        self._create_tables()
    
    def _get_guild_lock(self, guild_id: int) -> asyncio.Lock:
        """Get or create a lock for a specific guild."""
        if guild_id not in self.guild_locks:
            self.guild_locks[guild_id] = asyncio.Lock()
        return self.guild_locks[guild_id]
        
    def _process_text(self, text: Optional[str]) -> Optional[str]:
        """Replace {s} with a space in the given text."""
        if text and '{s}' in text:
            return text.replace('{s}', ' ')
        return text
        
    async def _process_member_update(self, member: discord.Member):
        """Internal method to process a member update."""
        try:
            # Get all role configs for the guild
            role_configs = await self.get_guild_prefixes_suffixes(member.guild.id)
            if not role_configs:
                return
                
            # Get the highest priority role with prefix/suffix
            role_info = await self.get_highest_priority_role(member, role_configs)
            
            if not role_info:
                # Clean up nickname if needed
                await self._clean_nickname(member)
                return
                
            # Update nickname with new prefix/suffix
            await self._update_nickname_with_role(member, role_info)
                
        except Exception as e:
            print(f"Error in _process_member_update for {member}: {e}")
    
    async def _clean_nickname(self, member: discord.Member) -> bool:
        """Clean up nickname by removing any existing prefixes/suffixes.
        
        Args:
            member: The member whose nickname to clean
            
        Returns:
            bool: True if the nickname was cleaned, False otherwise
        """
        if not member.nick:
            return False
            
        current_nick = member.nick
        clean_nick = current_nick
        
        # First, try to strip any existing prefix/suffix pattern using regex
        # This handles cases where the prefix/suffix might not be in the current config
        import re
        
        # Pattern to match common prefix/suffix patterns
        # This will match anything that looks like [prefix]nickname[suffix] or similar
        pattern = r'^(\[.*?\]\s*)?(.+?)(\s*\[.*?\])?$'
        match = re.match(pattern, current_nick)
        
        if match:
            # The middle group is the clean nickname
            clean_nick = match.group(2).strip()
        
        # If no pattern was matched or the nickname didn't change, try the role-based cleaning
        if clean_nick == current_nick:
            # Get all role configs to check for existing prefixes/suffixes
            role_configs = await self.get_guild_prefixes_suffixes(member.guild.id)
            if not role_configs:
                return False
                
            # Process each role's prefix/suffix and remove them from the nickname
            for role_id, (prefix, suffix) in role_configs.items():
                # Process prefix if it exists
                if prefix:
                    processed_prefix = prefix
                    # Handle dynamic prefixes with {s} placeholder
                    if '{s}' in prefix:
                        processed_prefix = prefix.replace('{s}', '')
                    
                    # Remove any whitespace that might be after the prefix
                    processed_prefix = processed_prefix.rstrip()
                    
                    if clean_nick.startswith(processed_prefix):
                        clean_nick = clean_nick[len(processed_prefix):].lstrip()
                    else:
                        # Try with processed text if direct match fails
                        try:
                            processed_text = self._process_text(prefix)
                            if clean_nick.startswith(processed_text):
                                clean_nick = clean_nick[len(processed_text):].lstrip()
                        except Exception:
                            pass
                
                # Process suffix if it exists
                if suffix:
                    processed_suffix = suffix
                    # Handle dynamic suffixes with {s} placeholder
                    if '{s}' in suffix:
                        processed_suffix = suffix.replace('{s}', '')
                    
                    # Remove any whitespace that might be before the suffix
                    processed_suffix = processed_suffix.lstrip()
                    
                    if clean_nick.endswith(processed_suffix):
                        clean_nick = clean_nick[:-len(processed_suffix)].rstrip()
                    else:
                        # Try with processed text if direct match fails
                        try:
                            processed_text = self._process_text(suffix)
                            if clean_nick.endswith(processed_text):
                                clean_nick = clean_nick[:-len(processed_text)].rstrip()
                        except Exception:
                            pass
        
        # Only update if the nickname actually changed and is not empty
        if clean_nick and clean_nick != current_nick:
            try:
                await member.edit(nick=clean_nick)
                return True
            except (discord.Forbidden, discord.HTTPException) as e:
                print(f"Error updating nickname for {member}: {e}")
            return False
        return False
        
    async def _update_nickname_with_role(self, member: discord.Member, role_info: Tuple[int, Optional[str], Optional[str]]) -> bool:
        """Update a member's nickname with the given role's prefix/suffix.
        
        Args:
            member: The member whose nickname to update
            role_info: Tuple containing (role_id, prefix, suffix)
            
        Returns:
            bool: True if the nickname was updated, False otherwise
        """
        _, prefix, suffix = role_info
        current_nick = member.display_name
        
        # Convert standalone {s} to {none}
        if prefix and prefix.strip() == '{s}':
            prefix = '{none}'
        if suffix and suffix.strip() == '{s}':
            suffix = '{none}'
            
        # Skip processing if prefix or suffix is {none}
        if prefix and prefix.strip().lower() == '{none}':
            prefix = None
        if suffix and suffix.strip().lower() == '{none}':
            suffix = None
            
        # Check if prefix contains {s} and process it
        if prefix and '{s}' in prefix:
            prefix = self._process_text(prefix)
            
        # Check if suffix contains {s} and process it
        if suffix and '{s}' in suffix:
            suffix = self._process_text(suffix)
        
        # Start with a clean nickname (remove any existing prefixes/suffixes)
        clean_nick = current_nick.strip()
        role_configs = await self.get_guild_prefixes_suffixes(member.guild.id)
        
        if role_configs:
            for role_id, (p, s) in role_configs.items():
                # Handle {none} and {s} for role configs
                if p and p.strip().lower() == '{none}' or p and p.strip() == '{s}':
                    p = None
                if s and s.strip().lower() == '{none}' or s and s.strip() == '{s}':
                    s = None
                    
                # Process the role's prefix/suffix to handle {s}
                role_prefix = self._process_text(p) if p and '{s}' in p else p
                role_suffix = self._process_text(s) if s and '{s}' in s else s
                    
                # Remove existing prefixes (with or without space)
                if role_prefix:
                    # Try removing with space first (normal case)
                    if clean_nick.startswith(role_prefix):
                        clean_nick = clean_nick[len(role_prefix):].lstrip()
                    # Then try without space (user removed the space manually)
                    elif clean_nick.startswith(role_prefix.rstrip()):
                        clean_nick = clean_nick[len(role_prefix.rstrip()):].lstrip()
                
                # Remove existing suffixes (with or without space)
                if role_suffix:
                    # Try removing with space first (normal case)
                    if clean_nick.endswith(role_suffix):
                        clean_nick = clean_nick[:-len(role_suffix)].rstrip()
                    # Then try without space (user removed the space manually)
                    elif clean_nick.endswith(role_suffix.lstrip()):
                        clean_nick = clean_nick[:-len(role_suffix.lstrip())].rstrip()
        
        # Clean up any extra spaces that might remain
        clean_nick = ' '.join(clean_nick.split())
        
        # Apply new prefix and suffix without adding extra spaces
        new_nick = clean_nick
        if prefix:
            new_nick = f"{prefix}{new_nick}"
        if suffix:
            new_nick = f"{new_nick}{suffix}"
        
        # Trim to Discord's 32 character limit if needed
        if len(new_nick) > 32:
            # Calculate how much we need to trim from the base nickname
            base_len = len(clean_nick)
            prefix_len = len(prefix) if prefix else 0
            suffix_len = len(suffix) if suffix else 0
            
            # Calculate available space for the base nickname
            available_len = 32 - (prefix_len + suffix_len)
            
            if available_len <= 0:
                # If there's no space for the base nickname, just use the prefix/suffix
                new_nick = f"{prefix or ''}{suffix or ''}"[:32]
            else:
                # Trim the base nickname to fit
                trimmed_base = clean_nick[:available_len]
                new_nick = f"{prefix or ''}{trimmed_base}{suffix or ''}"
        
        # Check if update is actually needed
        if not new_nick or new_nick == current_nick:
            return False  # No change needed
        
        # If user doesn't have a nickname and we're setting it to their username
        if not member.nick and new_nick == member.name:
            return False  # No need to update
        
        # If the only difference is case, don't update
        if new_nick.lower() == current_nick.lower():
            return False
        
        # Add rate limiting check (5 seconds between updates for the same member)
        now = discord.utils.utcnow()
        last_update = self._last_nick_update.get(member.id)
        if last_update and (now - last_update).total_seconds() < 5:
            return False  # Rate limit
        
        try:
            await member.edit(nick=new_nick)
            self._last_nick_update[member.id] = now  # Update last update time
            return True
        except discord.Forbidden:
            # Don't retry if we don't have permission
            logger.warning(f"Missing permissions to update nickname for {member}")
            return False
        except discord.HTTPException as e:
            logger.error(f"Failed to update nickname for {member}: {e}")
            return False
    
    def _create_tables(self):
        """Create necessary database tables for role management."""
        cursor = self.db.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_role_prefix_suffix (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            prefix TEXT,
            suffix TEXT,
            PRIMARY KEY (guild_id, role_id),
            CHECK (prefix IS NOT NULL OR suffix IS NOT NULL)
        )
        ''')
        self.db.commit()
    
    async def set_role_prefix_suffix(self, guild_id: int, role_id: int, 
                                  prefix: Optional[str] = None, 
                                  suffix: Optional[str] = None) -> bool:
        """
        Set or update prefix/suffix for a role in a guild.
        At least one of prefix or suffix must be provided.
        """
        if prefix is None and suffix is None:
            return False
            
        with self.db:
            cursor = self.db.cursor()
            cursor.execute('''
            INSERT OR REPLACE INTO guild_role_prefix_suffix (guild_id, role_id, prefix, suffix)
            VALUES (?, ?, ?, ?)
            ''', (guild_id, role_id, prefix, suffix))
            return cursor.rowcount > 0
    
    async def get_role_prefix_suffix(self, guild_id: int, role_id: int) -> Optional[Dict[str, Any]]:
        """Get prefix/suffix for a specific role in a guild.
        
        Any emojis in the prefix or suffix will be transformed to their Unicode equivalents.
        """
        cursor = self.db.cursor()
        cursor.execute('''
        SELECT prefix, suffix FROM guild_role_prefix_suffix
        WHERE guild_id = ? AND role_id = ?
        ''', (guild_id, role_id))
        
        result = cursor.fetchone()
        if result:
            return {
                'prefix': transform_emoji(result[0]) if result[0] else None,
                'suffix': transform_emoji(result[1]) if result[1] else None
            }
        return None
    
    async def remove_role_prefix_suffix(self, guild_id: int, role_id: int) -> bool:
        """Remove prefix/suffix configuration for a role."""
        with self.db:
            cursor = self.db.cursor()
            cursor.execute('''
            DELETE FROM guild_role_prefix_suffix
            WHERE guild_id = ? AND role_id = ?
            ''', (guild_id, role_id))
            return cursor.rowcount > 0
    
    async def get_guild_prefixes_suffixes(self, guild_id: int, use_cache: bool = True) -> Dict[int, Tuple[Optional[str], Optional[str]]]:
        """Get all role prefix/suffix configurations for a guild with caching.
        
        Any emojis in the prefix or suffix will be transformed to their Unicode equivalents.
        """
        # Check cache first
        if use_cache and guild_id in self.role_cache:
            cache_entry = self.role_cache[guild_id]
            if time.time() < cache_entry['expires']:
                return cache_entry['roles']
        
        # Not in cache or cache expired, fetch from database
        cursor = self.db.cursor()
        cursor.execute('''
        SELECT role_id, prefix, suffix FROM guild_role_prefix_suffix
        WHERE guild_id = ?
        ''', (guild_id,))
        
        # Transform emojis in prefixes and suffixes
        roles = {}
        for row in cursor.fetchall():
            role_id, prefix, suffix = row
            # Transform emojis in both prefix and suffix
            roles[role_id] = (
                transform_emoji(prefix) if prefix else None,
                transform_emoji(suffix) if suffix else None
            )
        
        # Update cache
        self.role_cache[guild_id] = {
            'roles': roles,
            'expires': time.time() + self.cache_duration
        }
        
        return roles
    
    def invalidate_guild_cache(self, guild_id: int):
        """Remove guild's role config from cache."""
        self.role_cache.pop(guild_id, None)
    
    async def get_highest_priority_role(self, member: discord.Member, role_configs: Dict[int, Tuple[Optional[str], Optional[str]]]) -> Optional[Tuple[int, Optional[str], Optional[str]]]:
        """Get the highest priority role with prefix/suffix for a member."""
        member_role_ids = {role.id for role in member.roles}
        
        # Get all roles that the member has and have configs, sorted by position (highest first)
        matching_roles = []
        for role in member.roles:
            if role.id in role_configs:
                prefix, suffix = role_configs[role.id]
                matching_roles.append((role.position, role.id, prefix, suffix))
        
        if not matching_roles:
            return None
            
        # Sort by position (highest first) and return the first one
        _, role_id, prefix, suffix = max(matching_roles, key=lambda x: x[0])
        return role_id, prefix, suffix
    
    async def update_member_nickname(self, member: discord.Member) -> bool:
        """
        Queue a member's nickname update based on their highest priority role.
        Returns True if member was queued, False otherwise.
        """
        try:
            # Check if we have permission to manage nicknames
            if not member.guild.me.guild_permissions.manage_nicknames:
                return False
                
            # Check if the bot has a higher role than the member
            if member.top_role >= member.guild.me.top_role and member != member.guild.owner:
                return False
                
            # Add to update queue
            await self.update_queue.add_member(member)
            return True
                
        except Exception as e:
            print(f"Error queuing member update for {member}: {e}")
            return False
    
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Handle member updates (nickname or role changes)."""
        # Check if nickname changed or roles changed
        if before.nick != after.nick or before.roles != after.roles:
            await self.update_member_nickname(after)
    
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member joins."""
        await self.update_member_nickname(member)
        
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """Clean up database entries when a role is deleted."""
        # Remove the role's prefix/suffix configuration from the database
        success = await self.remove_role_prefix_suffix(role.guild.id, role.id)
        
        if success:
            # Invalidate the cache for this guild
            self.invalidate_guild_cache(role.guild.id)
    
    @app_commands.command(name="set", description="Set a role's prefix and suffix (SEO: title)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        role="The role to set prefix/suffix for",
        prefix="The prefix to add | {s} = spaces | {none} = no prefix",
        suffix="The suffix to add | {s} = spaces | {none} = no suffix"
    )
    async def set_prefix_suffix(self, interaction: discord.Interaction, 
                              role: discord.Role,
                              prefix: Optional[str] = None,
                              suffix: Optional[str] = None):
        """Set or update a role's prefix and/or suffix."""
        if prefix is None and suffix is None:
            await interaction.response.send_message(
                "You must provide at least a prefix or a suffix.",
                ephemeral=True
            )
            return
            
        # Clean up empty strings
        prefix = prefix.strip() if prefix else None
        suffix = suffix.strip() if suffix else None
        
        if prefix and len(prefix) > 15:
            await interaction.response.send_message(
                "Prefix must be 10 characters or less.",
                ephemeral=True
            )
            return
            
        if suffix and len(suffix) > 15:
            await interaction.response.send_message(
                "Suffix must be 15 characters or less.",
                ephemeral=True
            )
            return
        
        # Defer the response since this might take a while
        await interaction.response.defer(ephemeral=True)
        
        guild_lock = self._get_guild_lock(interaction.guild.id)
        
        # Try to acquire the lock with a timeout of 5 seconds
        try:
            await asyncio.wait_for(guild_lock.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Another operation is currently in progress. Please try again in a moment.",
                ephemeral=True
            )
            return
        
        try:
            # Get current config to check what we're replacing
            current = await self.get_role_prefix_suffix(interaction.guild.id, role.id)
            current_prefix = current['prefix'] if current else None
            current_suffix = current['suffix'] if current else None
        
            # Process prefix and suffix to replace {s} with a space
            prefix = self._process_text(prefix)
            suffix = self._process_text(suffix)
            
            # Update or insert the new configuration
            success = await self.set_role_prefix_suffix(
                interaction.guild.id, 
                role.id, 
                prefix, 
                suffix
            )
            
            if not success:
                await interaction.followup.send(
                    "Failed to update role configuration.",
                    ephemeral=True
                )
                return
            
            # Invalidate cache for this guild
            self.invalidate_guild_cache(interaction.guild.id)
            
            # Update all members with this role
            updated = 0
            for member in interaction.guild.members:
                if role in member.roles and member.nick:
                    current_nick = member.nick
                    clean_nick = current_nick
                    
                    # If we're changing the prefix, remove the old one
                    if current_prefix and current_prefix in clean_nick and prefix != current_prefix:
                        clean_nick = clean_nick.replace(current_prefix, '')
                    
                    # If we're changing the suffix, remove the old one
                    if current_suffix and current_suffix in clean_nick and suffix != current_suffix:
                        clean_nick = clean_nick.replace(current_suffix, '')
                    
                    # Only update if the nickname actually changed
                    if clean_nick != current_nick and clean_nick:
                        try:
                            await member.edit(nick=clean_nick)
                            updated += 1
                        except (discord.Forbidden, discord.HTTPException):
                            continue
            
            # Queue all members with this role for update with new prefix/suffix
            queued = 0
            for member in interaction.guild.members:
                if role in member.roles:
                    if await self.update_member_nickname(member):
                        queued += 1
                        # Small delay to prevent rate limiting
                        await asyncio.sleep(0.1)
        
            # Send response
            parts = []
            if prefix is not None:
                parts.append(f"prefix: `{prefix}`")
            if suffix is not None:
                parts.append(f"suffix: `{suffix}`")
                
            action = "Updated" if current else "Set"
            await interaction.followup.send(
                f"{action} {role.mention} with {', '.join(parts)}. "
                f"Cleaned up {updated} nicknames and queued {queued} members for update.",
                allowed_mentions=discord.AllowedMentions.none()
            )
                
        except Exception as e:
            await interaction.followup.send(
                f"An error occurred: {str(e)}",
                ephemeral=True
            )
            raise
            
        finally:
            # Always release the lock when done
            if guild_lock.locked():
                guild_lock.release()
    
    @app_commands.command(name="remove", description="Remove a role's prefix and suffix (SEO: title)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(role="The role to remove prefix/suffix from")
    async def remove_prefix_suffix(self, interaction: discord.Interaction, role: discord.Role):
        """Remove prefix/suffix from a role and clean up all affected nicknames."""
        # Defer the response since this might take a while
        await interaction.response.defer(ephemeral=True)
        
        guild_lock = self._get_guild_lock(interaction.guild.id)
        
        # Try to acquire the lock with a timeout of 5 seconds
        try:
            await asyncio.wait_for(guild_lock.acquire(), timeout=5.0)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Another operation is currently in progress. Please try again in a moment.",
                ephemeral=True
            )
            return
        
        try:
            # Check if role has any config
            current = await self.get_role_prefix_suffix(interaction.guild.id, role.id)
            if not current or (current['prefix'] is None and current['suffix'] is None):
                await interaction.followup.send(
                    f"{role.mention} doesn't have any prefix or suffix configured.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none()
                )
                return
            
            # Get the current prefix and suffix to remove
            prefix_to_remove = current['prefix']
            suffix_to_remove = current['suffix']
            
            # Process {s} in the prefix/suffix if they exist
            if prefix_to_remove and '{s}' in prefix_to_remove:
                prefix_to_remove = self._process_text(prefix_to_remove)
                
            if suffix_to_remove and '{s}' in suffix_to_remove:
                suffix_to_remove = self._process_text(suffix_to_remove)
            
            # Remove the role configuration
            await self.remove_role_prefix_suffix(interaction.guild.id, role.id)
            
            # Invalidate cache for this guild
            self.invalidate_guild_cache(interaction.guild.id)
            
            # Process all members with this role to clean up their nicknames
            updated = 0
            for member in interaction.guild.members:
                if role in member.roles and member.nick:
                    current_nick = member.nick
                    clean_nick = current_nick
                
                    # Remove the prefix if it exists in the nickname
                    if prefix_to_remove:
                        # Try removing from start
                        if clean_nick.startswith(prefix_to_remove):
                            clean_nick = clean_nick[len(prefix_to_remove):].lstrip()
                        # Try removing from middle/end
                        else:
                            clean_nick = clean_nick.replace(prefix_to_remove, '').strip()
                    
                    # Remove the suffix if it exists in the nickname
                    if suffix_to_remove:
                        # Try removing from end
                        if clean_nick.endswith(suffix_to_remove):
                            clean_nick = clean_nick[:-len(suffix_to_remove)].rstrip()
                        # Try removing from middle/start
                        else:
                            clean_nick = clean_nick.replace(suffix_to_remove, '').strip()
                    
                    # Clean up any double spaces
                    clean_nick = ' '.join(clean_nick.split())
                    
                    # Only update if the nickname actually changed
                    if clean_nick != current_nick and clean_nick:
                        try:
                            await member.edit(nick=clean_nick)
                            updated += 1
                        except (discord.Forbidden, discord.HTTPException):
                            continue
            
            # If any members still have the role, update their nicknames based on other roles
            queued = 0
            for member in interaction.guild.members:
                if role in member.roles:
                    if await self.update_member_nickname(member):
                        queued += 1
            
            await interaction.followup.send(
                f"Removed prefix/suffix from {role.mention}. "
                f"Cleaned up {updated} nicknames and queued {queued} members for update.",
                allowed_mentions=discord.AllowedMentions.none()
            )
                
        except Exception as e:
            await interaction.followup.send(
                f"An error occurred: {str(e)}",
                ephemeral=True
            )
            raise
            
        finally:
            # Always release the lock when done
            if guild_lock.locked():
                guild_lock.release()
    
    @app_commands.command(name="sync", description="Sync nicknames for members with the specified role or all roles with prefixes/suffixes (SEO: title)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        role="The role to sync nicknames for (leave empty to sync all roles)"
    )
    async def sync_nicknames(self, interaction: discord.Interaction, role: Optional[discord.Role] = None):
        """Sync nicknames for members with the specified role or all roles with prefixes/suffixes."""
        # Defer the response since this might take a while
        await interaction.response.defer(ephemeral=True)
        
        guild_lock = self._get_guild_lock(interaction.guild.id)
        
        try:
            # Try to acquire the lock with a timeout of 5 seconds
            try:
                await asyncio.wait_for(guild_lock.acquire(), timeout=5.0)
            except asyncio.TimeoutError:
                await interaction.followup.send(
                    "Another operation is currently in progress. Please try again in a moment.",
                    ephemeral=True
                )
                return
                
            try:
                role_configs = await self.get_guild_prefixes_suffixes(interaction.guild.id, use_cache=False)
                
                if not role_configs:
                    await interaction.followup.send(
                        "No roles with prefixes/suffixes found in this server.",
                        ephemeral=True
                    )
                    return
                
                # Filter by role if specified
                if role:
                    if role.id not in role_configs:
                        await interaction.followup.send(
                            f"{role.mention} doesn't have any prefix or suffix configured.",
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none()
                        )
                        return
                    role_configs = {role.id: role_configs[role.id]}
                
                # Process members with the specified roles
                updated = 0
                processed_members = set()
                
                for member in interaction.guild.members:
                    # Skip if we've already processed this member
                    if member.id in processed_members:
                        continue
                        
                    # Check if member has any of the roles we're syncing
                    member_roles = {r.id for r in member.roles}
                    has_relevant_role = any(role_id in member_roles for role_id in role_configs)
                    
                    if has_relevant_role:
                        if await self.update_member_nickname(member):
                            updated += 1
                            processed_members.add(member.id)
                        # Small delay to prevent rate limiting
                        await asyncio.sleep(0.1)
                
                role_mention = role.mention if role else "all roles"
                await interaction.followup.send(
                    f"✅ Successfully synced nicknames for {updated} members with {role_mention}.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none()
                )
                
            finally:
                # Always release the lock when done
                if guild_lock.locked():
                    guild_lock.release()
                    
        except Exception as e:
            if guild_lock.locked():
                guild_lock.release()
            raise e
    
    @app_commands.command(name="list", description="List all roles with prefixes/suffixes (SEO: title)")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_prefix_suffix(self, interaction: discord.Interaction):
        """List all roles with prefixes/suffixes."""
        role_configs = await self.get_guild_prefixes_suffixes(interaction.guild.id)
        if not role_configs:
            await interaction.response.send_message(
                "No roles have prefixes or suffixes configured.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="Role Prefixes/Suffixes",
            color=discord.Color.blue()
        )
        
        for role_id, (prefix, suffix) in role_configs.items():
            role = interaction.guild.get_role(role_id)
            if not role:
                continue
                
            parts = []
            if prefix:
                parts.append(f"Prefix: `{prefix}`")
            if suffix:
                parts.append(f"Suffix: `{suffix}`")
                
            embed.add_field(
                name=role.name,
                value="\n".join(parts) or "No prefix/suffix",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Card(bot))
