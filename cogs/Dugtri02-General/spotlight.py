# Set an initial role and a target role to randomly rotate users out of that target role
# decently customizable! current implementation maxes out at 8 users but that can be changed

import discord; from discord import app_commands; from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone; import pytz
import random, logging, asyncio, sqlite3; from optparse import Option
from typing import Dict, List, Optional, Set, Tuple, Deque
from collections import deque; from dataclasses import dataclass, field


logger = logging.getLogger(__name__)

@dataclass
class SpotlightConfig:
    guild_id: int
    initial_role_id: int
    target_role_id: int
    max_users: int
    id: Optional[int] = None  # Will be set when loaded from DB
    last_rotation: Optional[datetime] = None
    cache_expiry: Optional[datetime] = None
    remove_when_offline: bool = False  # Whether to remove users when they go offline
    prioritize_active: bool = False  # Whether to prioritize active users in rotation
    ignore_timed_out: bool = False  # Whether to ignore timed out users in rotation
    blacklisted_role_id: Optional[int] = None  # Role ID to exclude from rotation
    always_replace_current: bool = False  # Whether to always replace current spotlight members
    blacklisted_role_id_2: Optional[int] = None  # Role ID to exclude from rotation
    blacklisted_role_id_3: Optional[int] = None  # Role ID to exclude from rotation
    blacklisted_role_id_4: Optional[int] = None  # Role ID to exclude from rotation

class SpotlightCache:
    def __init__(self):
        self._cache: Dict[int, SpotlightConfig] = {}
        self._role_mapping: Dict[int, Set[Tuple[int, int]]] = {}
        self._lock = asyncio.Lock()
    
    async def get(self, guild_id: int) -> Optional[SpotlightConfig]:
        async with self._lock:
            config = self._cache.get(guild_id)
            if config and config.cache_expiry and datetime.utcnow() > config.cache_expiry:
                del self._cache[guild_id]
                self._cleanup_mapping(guild_id)
                return None
            return config
    
    async def set(self, config: SpotlightConfig, expiry_hours: int = 24):
        async with self._lock:
            self._cleanup_mapping(config.guild_id)
            config.cache_expiry = datetime.utcnow() + timedelta(hours=expiry_hours)
            self._cache[config.guild_id] = config
            
            for role_id in [config.initial_role_id, config.target_role_id]:
                if role_id not in self._role_mapping:
                    self._role_mapping[role_id] = set()
                self._role_mapping[role_id].add((config.guild_id, config.initial_role_id, config.target_role_id))
    
    async def delete(self, guild_id: int):
        async with self._lock:
            if guild_id in self._cache:
                self._cleanup_mapping(guild_id)
                del self._cache[guild_id]
    
    def _cleanup_mapping(self, guild_id: int):
        for role_id in list(self._role_mapping.keys()):
            mappings = self._role_mapping[role_id]
            to_remove = {m for m in mappings if m[0] == guild_id}
            for m in to_remove:
                mappings.discard(m)
            if not mappings:
                del self._role_mapping[role_id]
    
    async def get_affected_configs(self, role_id: int) -> List[Tuple[int, int, int]]:
        async with self._lock:
            return list(self._role_mapping.get(role_id, set()))

class RoleOperation:
    def __init__(self, member: discord.Member, role: discord.Role, add: bool):
        self.member = member
        self.role = role
        self.add = add
        self.attempts = 0
        self.last_attempt: Optional[datetime] = None

class Spotlight(commands.GroupCog, name="spotlight"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.cache = SpotlightCache()
        self.role_queue: Deque[RoleOperation] = deque()
        self.role_queue_lock = asyncio.Lock()
        self.role_processing = False
        self.rotation_task = self.rotate_spotlight.start()
        self.role_processor_task = self.bot.loop.create_task(self.process_role_queue())
        self._create_tables()
        self._last_config_index = 0  # Track the last processed config
    
    def cog_unload(self):
        self.rotation_task.cancel()
        if hasattr(self, 'role_processor_task'):
            self.role_processor_task.cancel()
    
    def _create_tables(self):
        cursor = self.db.cursor()
        # Create guild settings table if it doesn't exist
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS spotlight_guild_settings (
            guild_id INTEGER PRIMARY KEY,
            max_configs INTEGER NOT NULL DEFAULT 1
        )
        ''')
        # Drop the backup table if it exists
        cursor.execute("DROP TABLE IF EXISTS spotlight_backup")
        
        # Check if the old table exists and has data
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='spotlight'")
        old_table_exists = cursor.fetchone() is not None
        
        if old_table_exists:
            # Check if the old table has the remove_when_offline column
            cursor.execute("PRAGMA table_info(spotlight)")
            columns = [col[1] for col in cursor.fetchall()]
            has_new_column = 'blacklisted_role_id_2' in columns
            
            if not has_new_column:                
                # Add the new columns to the old table with default values, one at a time
                try:
                    cursor.execute('''
                    ALTER TABLE spotlight 
                    ADD COLUMN blacklisted_role_id_2 BOOLEAN NOT NULL DEFAULT 0
                    ''')
                    
                    cursor.execute('''
                    ALTER TABLE spotlight 
                    ADD COLUMN blacklisted_role_id_3 BOOLEAN NOT NULL DEFAULT 0
                    ''')

                    cursor.execute('''
                    ALTER TABLE spotlight 
                    ADD COLUMN blacklisted_role_id_4 BOOLEAN NOT NULL DEFAULT 0
                    ''')
                    
                    # If there was a backup table with the new schema, drop it
                    cursor.execute("DROP TABLE IF EXISTS spotlight_new")
                    logger.info("Successfully added new columns to spotlight table")
                except sqlite3.OperationalError as e:
                    logger.error(f"Error adding columns to spotlight table: {e}")
                    # If the columns already exist, we can continue
                    if "duplicate column" not in str(e).lower():
                        raise
                
                # Commit the changes
                self.db.commit()
                logger.info("Added blacklisted_role_id_3, blacklisted_role_id_4 columns to spotlight table")
        else:
            # Create the table from scratch with the new schema
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS spotlight (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                initial_role_id INTEGER NOT NULL,
                target_role_id INTEGER NOT NULL,
                max_users INTEGER NOT NULL,
                rotation_interval_hours INTEGER NOT NULL DEFAULT 1,
                last_rotation TIMESTAMP,
                remove_when_offline BOOLEAN NOT NULL DEFAULT 0,
                prioritize_active BOOLEAN NOT NULL DEFAULT 0,
                blacklisted_role_id INTEGER,
                ignore_timed_out BOOLEAN NOT NULL DEFAULT 0,
                always_replace_current BOOLEAN NOT NULL DEFAULT 0,
                blacklisted_role_id_2 INTEGER,
                blacklisted_role_id_3 INTEGER,
                blacklisted_role_id_4 INTEGER,
                UNIQUE(guild_id, initial_role_id, target_role_id)
            )
            ''')
            
        self.db.commit()
    
    async def get_guild_max_configs(self, guild_id: int) -> int:
        """Get the maximum number of spotlight configurations allowed for a guild"""
        cursor = self.db.cursor()
        cursor.execute(
            'SELECT max_configs FROM spotlight_guild_settings WHERE guild_id = ?',
            (guild_id,)
        )
        result = cursor.fetchone()
        return result[0] if result else 1  # Default to 1 if not set
    
    async def set_guild_max_configs(self, guild_id: int, max_configs: int) -> None:
        """Set the maximum number of spotlight configurations for a guild"""
        cursor = self.db.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO spotlight_guild_settings (guild_id, max_configs) VALUES (?, ?)',
            (guild_id, max_configs)
        )
        self.db.commit()
    
    async def get_configs(self, guild_id: int) -> List[SpotlightConfig]:
        """Get all spotlight configurations for a guild"""
        cursor = self.db.cursor()
        cursor.execute(
            'SELECT id, initial_role_id, target_role_id, max_users, last_rotation, remove_when_offline '
            'FROM spotlight WHERE guild_id = ?',
            (guild_id,)
        )
        
        configs = []
        for row in cursor.fetchall():
            config = SpotlightConfig(
                guild_id=guild_id,
                initial_role_id=row[1],
                target_role_id=row[2],
                max_users=row[3],
                id=row[0],
                last_rotation=datetime.fromisoformat(row[4]) if row[4] else None,
                remove_when_offline=bool(row[5]) if row[5] is not None else False
            )
            configs.append(config)
        
        return configs
    
    # Add the rest of the methods from the previous implementation
    # (save_config, delete_config, on_guild_role_delete, rotate_spotlight, etc.)
    
    @app_commands.command(name="set", description="Set up or update spotlight configuration")
    @app_commands.describe(
        initial_role="The role to select members from",
        target_role="The role to assign to selected members",
        max_users="Maximum number of members to select | Max: 8",
        rotation_interval="How often to rotate members (default: 1h)",
        remove_when_offline="Whether to remove users when they go offline (default: False)",
        prioritize_active="Whether to prioritize active members (default: False)",
        ignore_timed_out="Whether to ignore timed out members (default: False)",
        always_replace_current="Whether to always replace the current user (default: False)",
    )
    @app_commands.choices(rotation_interval=[
        # app_commands.Choice(name="1 minute (debug)", value=5),  # 1 minute for debugging
        app_commands.Choice(name="1 hour", value=1),
        app_commands.Choice(name="2 hours", value=2),
        app_commands.Choice(name="3 hours", value=3),
        app_commands.Choice(name="6 hours", value=6),
        app_commands.Choice(name="12 hours", value=12),
        app_commands.Choice(name="24 hours", value=24),
        app_commands.Choice(name="2 days", value=48),
        app_commands.Choice(name="3 days", value=72),
        app_commands.Choice(name="4 days", value=96),
        app_commands.Choice(name="5 days", value=120),
        app_commands.Choice(name="6 days", value=144),
        app_commands.Choice(name="1 week", value=168),
        app_commands.Choice(name="2 weeks", value=336),
        app_commands.Choice(name="3 weeks", value=504),
        app_commands.Choice(name="1 month", value=672),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def set_spotlight(
        self,
        interaction: discord.Interaction,
        initial_role: discord.Role,
        target_role: discord.Role,
        max_users: int,
        rotation_interval: int = 1,
        remove_when_offline: bool = False,
        prioritize_active: bool = False,
        ignore_timed_out: bool = False,
        always_replace_current: bool = False,
    ):

        if rotation_interval == 5 and interaction.user.id != 311456723682590721:
            await interaction.response.send_message("❌ You do not have permission to use this interval.", ephemeral=True)
            return

        """Set up or update spotlight configuration"""
        if max_users < 1:
            await interaction.response.send_message("❌ Maximum users must be at least 1.", ephemeral=True)
            return
        
        if max_users > 8:
            await interaction.response.send_message("❌ Maximum users must be at most 8.", ephemeral=True)
            return
        
        if initial_role.position >= interaction.guild.me.top_role.position:
            await interaction.response.send_message(
                "❌ I can't manage the initial role because it's higher than my highest role.",
                ephemeral=True
            )
            return
            
        if target_role.position >= interaction.guild.me.top_role.position:
            await interaction.response.send_message(
                "❌ I can't manage the target role because it's higher than my highest role.",
                ephemeral=True
            )
            return
        
        if initial_role.id == target_role.id:
            await interaction.response.send_message("❌ Initial role and target role must be different.", ephemeral=True)
            return
            
        # Initialize cursor for database operations
        cursor = self.db.cursor()
            
        # Check if this target role is already being used in any other config
        cursor.execute(
            'SELECT initial_role_id FROM spotlight WHERE guild_id = ? AND target_role_id = ?',
            (interaction.guild_id, target_role.id)
        )
        existing = cursor.fetchone()
        if existing and (existing[0] != initial_role.id or not interaction.data.get('options')):
            existing_initial_role = interaction.guild.get_role(existing[0])
            role_mention = existing_initial_role.mention if existing_initial_role else f'Role ID: {existing[0]}'
            await interaction.response.send_message(
                f"❌ {target_role.mention} is already being targeted by {role_mention}. "
                "Each target role can only be assigned by one initial role.",
                ephemeral=True
            )
            return
        
        # Check if this exact configuration already exists
        cursor = self.db.cursor()
        cursor.execute(
            'SELECT id, remove_when_offline FROM spotlight WHERE guild_id = ? AND initial_role_id = ? AND target_role_id = ?',
            (interaction.guild_id, initial_role.id, target_role.id)
        )
        
        if cursor.fetchone():
            # Update existing configuration
            cursor.execute('''
                UPDATE spotlight 
                SET max_users = ?, rotation_interval_hours = ?, remove_when_offline = ?, prioritize_active = ?, ignore_timed_out = ?, always_replace_current = ?
                WHERE guild_id = ? AND initial_role_id = ? AND target_role_id = ?
            ''', (
                max_users,
                rotation_interval,
                int(remove_when_offline),
                int(prioritize_active),
                int(ignore_timed_out),
                int(always_replace_current),
                interaction.guild_id,
                initial_role.id,
                target_role.id
            ))
            action = "updated"
        else:
            # Check if we've reached the maximum configurations for this guild
            cursor.execute('SELECT COUNT(*) FROM spotlight WHERE guild_id = ?', (interaction.guild_id,))
            count = cursor.fetchone()[0]
            max_configs = await self.get_guild_max_configs(interaction.guild_id)
            
            if count >= max_configs:
                embed = discord.Embed(
                    title="❌ Spotlight Configuration Limit Reached",
                    description=f"You can only have up to {max_configs} spotlight configurations per server.\n"
                                "Please remove an existing configuration before adding a new one or request a higher limit in the support server: https://discord.gg/exwPCtMEsD",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
                
            # Insert new configuration
            cursor.execute('''
                INSERT INTO spotlight 
                (guild_id, initial_role_id, target_role_id, max_users, rotation_interval_hours, last_rotation, remove_when_offline, prioritize_active, ignore_timed_out, always_replace_current)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                interaction.guild_id,
                initial_role.id,
                target_role.id,
                max_users,
                rotation_interval,
                None,  # Will be set on first rotation
                int(remove_when_offline),
                int(prioritize_active),
                int(ignore_timed_out),
                int(always_replace_current),
            ))
            action = "configured"
            
        self.db.commit()
        
        # Invalidate cache for this guild
        await self.cache.delete(interaction.guild_id)
        
        message = (
            f"✅ Spotlight {action}!\n"
            f"• Initial role: {initial_role.mention}\n"
            f"• Target role: {target_role.mention}\n"
            f"• Max users: {max_users}\n"
            f"• Remove when offline: {'`True`' if remove_when_offline else '`False`'}\n"
            f"• Rotation interval: {rotation_interval} hour(s)\n"
            f"• Prioritize active: {'`True`' if prioritize_active else '`False`'}\n"
            f"• Ignore timed out: {'`True`' if ignore_timed_out else '`False`'}\n"
            f"• Always replace current: {'`True`' if always_replace_current else '`False`'}"
        )
        await interaction.response.send_message(
            message,
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True
        )
        
    async def config_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete for spotlight configurations"""
        configs = await self.get_configs(interaction.guild_id)
        if not configs:
            return []
            
        guild = interaction.guild
        choices = []
        
        for config in configs:
            initial_role = guild.get_role(config.initial_role_id)
            target_role = guild.get_role(config.target_role_id)
            
            if not all([initial_role, target_role]):
                continue
                
            label = f"{initial_role.name} → {target_role.name}"
            if current.lower() in label.lower():
                choices.append(app_commands.Choice(
                    name=label,
                    value=str(config.id)
                ))
        
        return choices[:25]

    @app_commands.command(name="blacklist", description="Blacklist a role from being selected")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        config="The configuration to update",
        role="The role to blacklist",
        slot="Which blacklist slot to use (1-4)",
        option="Whether to add or remove the role from the blacklist"
    )
    @app_commands.autocomplete(config=config_autocomplete)
    @app_commands.choices(slot=[
        app_commands.Choice(name="Slot 1", value=1),
        app_commands.Choice(name="Slot 2", value=2),
        app_commands.Choice(name="Slot 3", value=3),
        app_commands.Choice(name="Slot 4", value=4),
    ],
    option=[
        app_commands.Choice(name="Add", value=1),
        app_commands.Choice(name="Remove", value=2),
        app_commands.Choice(name="Clear All", value=0)
    ]
    )
    async def blacklist_role(
        self,
        interaction: discord.Interaction,
        config: str,
        option: int,
        role: Optional[discord.Role] = None,
        slot: Optional[int] = None
    ):
        """Manage blacklisted roles for spotlight configuration"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Get the config from database
            cursor = self.db.cursor()
            try:
                config_id = int(config)
            except ValueError:
                cursor.close()
                return await interaction.followup.send("❌ Invalid configuration ID. Please select a valid configuration.", ephemeral=True)
                
            cursor.execute(
                """
                SELECT id, guild_id, initial_role_id, target_role_id, 
                       blacklisted_role_id, blacklisted_role_id_2, 
                       blacklisted_role_id_3, blacklisted_role_id_4
                FROM spotlight 
                WHERE id = ? AND guild_id = ?
                """,
                (config_id, interaction.guild_id)
            )
            row = cursor.fetchone()
            
            if not row:
                cursor.close()
                return await interaction.followup.send("❌ Configuration not found.", ephemeral=True)
                
            config_id, guild_id, initial_role_id, target_role_id, \
            blacklisted_role_id, blacklisted_role_id_2, \
            blacklisted_role_id_3, blacklisted_role_id_4 = row
            
            # Clear all blacklisted roles if option is 0 (Clear All)
            if option == 0:
                try:
                    # Instead of setting to NULL, we'll remove the entries by not including them in the UPDATE
                    cursor.execute(
                        """
                        UPDATE spotlight 
                        SET blacklisted_role_id = 0,
                            blacklisted_role_id_2 = 0,
                            blacklisted_role_id_3 = 0,
                            blacklisted_role_id_4 = 0
                        WHERE id = ?
                        """,
                        (config_id,)
                    )
                    self.db.commit()
                    # Update cache
                    await self.cache.delete(interaction.guild_id)
                    cursor.close()
                    return await interaction.followup.send("✅ Cleared all blacklisted roles.", ephemeral=True)
                except Exception as e:
                    cursor.close()
                    logger.error(f"Error clearing blacklisted roles: {str(e)}")
                    return await interaction.followup.send("❌ An error occurred while clearing blacklisted roles.", ephemeral=True)
            
            # If removing a specific role
            if option == 2:
                if not role:
                    cursor.close()
                    return await interaction.followup.send("❌ Please specify a role to remove from the blacklist.", ephemeral=True)
                if not slot or slot < 1 or slot > 4:
                    cursor.close()
                    return await interaction.followup.send("❌ Please specify a valid slot (1-4) when removing a blacklisted role.", ephemeral=True)
                
                # Check if the role is blacklisted in the specified slot
                if (slot == 1 and blacklisted_role_id == role.id) or \
                   (slot == 2 and blacklisted_role_id_2 == role.id) or \
                   (slot == 3 and blacklisted_role_id_3 == role.id) or \
                   (slot == 4 and blacklisted_role_id_4 == role.id):
                    
                    try:
                        # Clear the specific slot by setting it to 0
                        column_name = f"blacklisted_role_id{'_' + str(slot) if slot > 1 else ''}"
                        update_query = f"""
                        UPDATE spotlight 
                        SET {column_name} = 0
                        WHERE id = ?
                        """
                        cursor.execute(update_query, (config_id,))
                        self.db.commit()
                        # Update cache
                        await self.cache.delete(interaction.guild_id)
                        cursor.close()
                        return await interaction.followup.send(f"✅ Removed <@&{role.id}> from blacklist slot {slot}.", ephemeral=True)
                    except Exception as e:
                        cursor.close()
                        logger.error(f"Error removing blacklisted role: {str(e)}")
                        return await interaction.followup.send("❌ An error occurred while removing the blacklisted role.", ephemeral=True)
                else:
                    cursor.close()
                    return await interaction.followup.send(f"❌ Role <@&{role.id}> is not in blacklist slot {slot}.", ephemeral=True)
            
            # If adding a role
            elif option == 1:
                if not role:
                    cursor.close()
                    return await interaction.followup.send("❌ Please specify a role to add to the blacklist.", ephemeral=True)
                if not slot or slot < 1 or slot > 4:
                    cursor.close()
                    return await interaction.followup.send("❌ Please specify a valid slot (1-4) when adding a blacklisted role.", ephemeral=True)
                
                # Check if role is already blacklisted in any slot
                if role.id in [blacklisted_role_id, blacklisted_role_id_2, blacklisted_role_id_3, blacklisted_role_id_4]:
                    cursor.close()
                    return await interaction.followup.send(f"❌ Role <@&{role.id}> is already blacklisted.", ephemeral=True)
                
                try:
                    # Add role to the specified slot using a parameterized query
                    column_name = f"blacklisted_role_id{'_' + str(slot) if slot > 1 else ''}"
                    update_query = f"""
                    UPDATE spotlight 
                    SET {column_name} = ?
                    WHERE id = ?
                    """
                    cursor.execute(update_query, (role.id, config_id))
                    self.db.commit()
                    # Update cache
                    await self.cache.delete(interaction.guild_id)
                    cursor.close()
                    return await interaction.followup.send(f"✅ Added <@&{role.id}> to blacklist slot {slot}.", ephemeral=True)
                except Exception as e:
                    cursor.close()
                    logger.error(f"Error adding blacklisted role: {str(e)}")
                    return await interaction.followup.send("❌ An error occurred while adding the blacklisted role.", ephemeral=True)
            
            else:
                cursor.close()
                return await interaction.followup.send("❌ Invalid option selected.", ephemeral=True)
                
        except Exception as e:
            if 'cursor' in locals() and cursor:
                cursor.close()
            logger.error(f"Error in blacklist_role: {str(e)}")
            await interaction.followup.send("❌ An error occurred while updating the blacklist.", ephemeral=True)
    
    @app_commands.command(name="edit", description="Edit an existing spotlight configuration")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    @app_commands.autocomplete(config=config_autocomplete)
    @app_commands.describe(
        config="The configuration to edit",
        initial_role="The role to select members from",
        target_role="The role to assign to selected members",
        max_users="Maximum number of members to select | Max: 8",
        rotation_interval="How often to rotate members (default: 1h)",
        remove_when_offline="Whether to remove users when they go offline (default: False)",
        prioritize_active="Whether to prioritize active members (default: False)",
        ignore_timed_out="Whether to ignore timed out members (default: False)",
        always_replace_current="Whether to always replace the current user (default: False)"
    )
    @app_commands.choices(rotation_interval=[
        app_commands.Choice(name="1 hour", value=1),
        app_commands.Choice(name="2 hours", value=2),
        app_commands.Choice(name="3 hours", value=3),
        app_commands.Choice(name="6 hours", value=6),
        app_commands.Choice(name="12 hours", value=12),
        app_commands.Choice(name="24 hours", value=24),
        app_commands.Choice(name="2 days", value=48),
        app_commands.Choice(name="3 days", value=72),
        app_commands.Choice(name="4 days", value=96),
        app_commands.Choice(name="5 days", value=120),
        app_commands.Choice(name="6 days", value=144),
        app_commands.Choice(name="1 week", value=168),
        app_commands.Choice(name="2 weeks", value=336),
        app_commands.Choice(name="3 weeks", value=504),
        app_commands.Choice(name="1 month", value=672)
    ])
    async def edit_spotlight(
        self,
        interaction: discord.Interaction,
        config: str,
        initial_role: discord.Role = None,
        target_role: discord.Role = None,
        max_users: int = None,
        rotation_interval: int = None,
        remove_when_offline: bool = None,
        prioritize_active: bool = None,
        ignore_timed_out: bool = None,
        always_replace_current: bool = None,
    ):
        """Edit an existing spotlight configuration"""
        # Defer the response since we'll be doing database operations
        await interaction.response.defer(ephemeral=True)
        
        # Get the existing configuration
        cursor = self.db.cursor()
        try:
            config_id = int(config)
        except ValueError:
            await interaction.followup.send("❌ Invalid configuration ID. Please select a valid configuration.", ephemeral=True)
            return
            
        cursor.execute(
            'SELECT id, initial_role_id, target_role_id, max_users, rotation_interval_hours, '
            'remove_when_offline, prioritize_active, ignore_timed_out, always_replace_current '
            'FROM spotlight WHERE id = ? AND guild_id = ?',
            (config_id, interaction.guild_id)
        )
        existing = cursor.fetchone()
        
        if not existing:
            await interaction.followup.send("❌ Could not find the specified configuration.", ephemeral=True)
            return
            
        # Unpack the existing values
        (config_id, old_initial_id, old_target_id, old_max_users, old_interval,
         old_remove_offline, old_prioritize, old_ignore_timeout, old_always_replace) = existing
        
        # Use existing values if no new value is provided
        if initial_role is None:
            initial_role = interaction.guild.get_role(old_initial_id)
        if target_role is None:
            target_role = interaction.guild.get_role(old_target_id)
        if max_users is None:
            max_users = old_max_users
        if rotation_interval is None:
            rotation_interval = old_interval
        if remove_when_offline is None:
            remove_when_offline = bool(old_remove_offline)
        if prioritize_active is None:
            prioritize_active = bool(old_prioritize)
        if ignore_timed_out is None:
            ignore_timed_out = bool(old_ignore_timeout)
        if always_replace_current is None:
            always_replace_current = bool(old_always_replace)
        
        # Validate the values
        if max_users < 1:
            await interaction.followup.send("❌ Maximum users must be at least 1.", ephemeral=True)
            return
        
        if max_users > 8:
            await interaction.followup.send("❌ Maximum users must be at most 8.", ephemeral=True)
            return
        
        if initial_role.position >= interaction.guild.me.top_role.position:
            await interaction.followup.send(
                "❌ I can't manage the initial role because it's higher than my highest role.",
                ephemeral=True
            )
            return
            
        if target_role.position >= interaction.guild.me.top_role.position:
            await interaction.followup.send(
                "❌ I can't manage the target role because it's higher than my highest role.",
                ephemeral=True
            )
            return
        
        if initial_role.id == target_role.id:
            await interaction.followup.send("❌ Initial role and target role must be different.", ephemeral=True)
            return
        
        # Check if the new target role is already used in another config
        if target_role.id != old_target_id:
            cursor.execute(
                'SELECT id FROM spotlight WHERE guild_id = ? AND target_role_id = ? AND id != ?',
                (interaction.guild_id, target_role.id, config_id)
            )
            if cursor.fetchone():
                await interaction.followup.send(
                    f"❌ {target_role.mention} is already being used as a target role in another configuration.",
                    ephemeral=True
                )
                return
        
        # Build the update query dynamically based on provided parameters
        update_fields = []
        params = []
        
        # Add fields to update if they were provided
        if initial_role is not None:
            update_fields.append("initial_role_id = ?")
            params.append(initial_role.id)
            
        if target_role is not None:
            update_fields.append("target_role_id = ?")
            params.append(target_role.id)
            
        if max_users is not None:
            update_fields.append("max_users = ?")
            params.append(max_users)
            
        if rotation_interval is not None:
            update_fields.append("rotation_interval_hours = ?")
            params.append(rotation_interval)
            
        if remove_when_offline is not None:
            update_fields.append("remove_when_offline = ?")
            params.append(int(remove_when_offline))
            
        if prioritize_active is not None:
            update_fields.append("prioritize_active = ?")
            params.append(int(prioritize_active))
            
        if ignore_timed_out is not None:
            update_fields.append("ignore_timed_out = ?")
            params.append(int(ignore_timed_out))
            
        if always_replace_current is not None:
            update_fields.append("always_replace_current = ?")
            params.append(int(always_replace_current))
        
        # If no fields to update, return early
        if not update_fields:
            await interaction.followup.send("❌ No changes provided.", ephemeral=True)
            return
        
        # Add the WHERE clause parameters
        params.extend([config_id, interaction.guild_id])
        
        # Build and execute the update query
        update_query = f"""
            UPDATE spotlight 
            SET {', '.join(update_fields)}
            WHERE id = ? AND guild_id = ?
        """
        
        cursor.execute(update_query, params)
        self.db.commit()
        
        # Invalidate cache for this guild
        await self.cache.delete(interaction.guild_id)
        
        # Build the response message
        message_parts = ["✅ Spotlight configuration updated!"]
        
        if initial_role is not None:
            message_parts.append(f"• Initial role: {initial_role.mention}")
            
        if target_role is not None:
            message_parts.append(f"• Target role: {target_role.mention}")
            
        if max_users is not None:
            message_parts.append(f"• Max users: {max_users}")
            
        if rotation_interval is not None:
            message_parts.append(f"• Rotation interval: {rotation_interval} hour(s)")
            
        if remove_when_offline is not None:
            message_parts.append(f"• Remove when offline: {'`True`' if remove_when_offline else '`False`'}")
            
        if prioritize_active is not None:
            message_parts.append(f"• Prioritize active: {'`True`' if prioritize_active else '`False`'}")
            
        if ignore_timed_out is not None:
            message_parts.append(f"• Ignore timed out: {'`True`' if ignore_timed_out else '`False`'}")
            
        if always_replace_current is not None:
            message_parts.append(f"• Always replace current: {'`True`' if always_replace_current else '`False`'}")
        
        # Send the response
        await interaction.followup.send(
            "\n".join(message_parts),
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True
        )
        
        # Trigger a rotation if the target role changed
        if target_role is not None and target_role.id != old_target_id:
            await self.force_rotate(interaction, str(config_id))

    @app_commands.command(name="force", description="Manually trigger a forced rotation of spotlight roles (SETS LAST_ROTATION TO CURRENT TIME)")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    @app_commands.autocomplete(config=config_autocomplete)
    async def force_rotate(
        self, 
        interaction: discord.Interaction, 
        config: str = None
    ):
        """Manually trigger a forced rotation of spotlight roles
        
        Parameters
        ----------
        config: Optional[str]
            The specific configuration to rotate. If not provided, all configurations will be rotated.
        """
        await interaction.response.defer(ephemeral=True)
        
        # Get configs for this guild
        all_configs = await self.get_configs(interaction.guild_id)
        if not all_configs:
            await interaction.followup.send("❌ No spotlight configurations found for this server.", ephemeral=True)
            return
        
        # Filter to specific config if provided
        if config:
            config_id = int(config)
            configs = [c for c in all_configs if c.id == config_id]
            if not configs:
                await interaction.followup.send("❌ Could not find the specified configuration.", ephemeral=True)
                return
        else:
            configs = all_configs
        
        success_count = 0
        guild = interaction.guild
        cursor = self.db.cursor()
        
        for config in configs:
            try:
                initial_role = guild.get_role(config.initial_role_id)
                target_role = guild.get_role(config.target_role_id)
                
                if not all([initial_role, target_role]):
                    logger.warning(f"Skipping config {config.id}: missing roles")
                    continue
                
                # Get all members with the initial role and group by status
                members_with_initial = [m for m in guild.members if initial_role in m.roles]
                
                if not members_with_initial:
                    logger.info(f"No members with initial role {initial_role.name} for config {config.id}")
                    continue
                
                # Group members into active (online, idle, dnd, streaming) and offline
                status_groups = {
                    'active': [],  # Includes online, idle, dnd, and streaming users
                    'offline': []
                }
                
                for member in members_with_initial:
                    # Check if user is streaming (purple status)
                    is_streaming = any(
                        activity.type == discord.ActivityType.streaming 
                        for activity in member.activities 
                        if hasattr(activity, 'type')
                    )
                    
                    # All non-offline statuses go into the active group
                    if is_streaming or member.status != discord.Status.offline:
                        status_groups['active'].append(member)
                    else:
                        status_groups['offline'].append(member)
                
                # Shuffle each status group to ensure randomness within groups
                for status in status_groups:
                    random.shuffle(status_groups[status])
                
                # Select members in priority order (active first, then offline)
                selected_members = []
                status_order = ['active', 'offline']
                
                for status in status_order:
                    # If we've already selected enough members, stop
                    if len(selected_members) >= config.max_users:
                        break
                        
                    # Take as many as we can from this status group
                    available = status_groups[status]
                    remaining_slots = config.max_users - len(selected_members)
                    selected_members.extend(available[:remaining_slots])
                
                # Get current spotlight members for this specific configuration
                current_spotlight = [m for m in guild.members 
                                  if target_role in m.roles 
                                  and initial_role in m.roles]

                # Queue role removals for all current spotlight members
                for member in current_spotlight:
                    # Only remove if they're not in the new selection
                    if member not in selected_members:
                        await self.queue_role_operation(member, target_role, False)
                
                # Queue role additions for new spotlight members
                for member in selected_members:
                    if member not in current_spotlight:
                        await self.queue_role_operation(member, target_role, True)
                
                # Update last rotation time for this config
                now = datetime.now(timezone.utc)
                cursor.execute(
                    'UPDATE spotlight SET last_rotation = ? WHERE id = ?',
                    (now.isoformat(), config.id)
                )
                
                success_count += 1
                
                logger.info(
                    f"FORCED rotation for config {config.id} in guild {interaction.guild_id} "
                    f"({len(selected_members)} members selected, {len(current_spotlight)} previous members)"
                )
                
            except Exception as e:
                logger.error(f"Error in forced rotation for config {config.id} in guild {interaction.guild_id}: {e}", exc_info=True)
        
        try:
            # Commit all database changes at once
            self.db.commit()
            
            # Invalidate cache for this guild
            await self.cache.delete(interaction.guild_id)
            
            if success_count > 0:
                await interaction.followup.send(
                    f"✅ Successfully FORCED rotation for {success_count} spotlight configuration(s). "
                    f"Randomly selected members from the initial role. "
                    f"Role updates are being processed in the background.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to rotate any spotlight configurations. Please check the logs for errors.",
                    ephemeral=True
                )
                
        except Exception as e:
            logger.error(f"Error committing rotation changes: {e}", exc_info=True)
            await interaction.followup.send(
                "❌ An error occurred while saving rotation changes. Please check the logs.",
                ephemeral=True
            )
    
    @app_commands.command(name="list", description="List all spotlight configurations for this server")
    @app_commands.checks.has_permissions(administrator=True)
    async def list_spotlights(self, interaction: discord.Interaction):
        """List all spotlight configurations for this server"""
        await interaction.response.defer(ephemeral=True)
        
        # Get configs with rotation_interval_hours and remove_when_offline
        cursor = self.db.cursor()
        cursor.execute('''
            SELECT id, initial_role_id, target_role_id, max_users, rotation_interval_hours, last_rotation, remove_when_offline, prioritize_active, ignore_timed_out, blacklisted_role_id, always_replace_current, blacklisted_role_id_2, blacklisted_role_id_3, blacklisted_role_id_4
            FROM spotlight 
            WHERE guild_id = ?
        ''', (interaction.guild_id,))
        
        configs = []
        for row in cursor.fetchall():
            config = SpotlightConfig(
                guild_id=interaction.guild_id,
                initial_role_id=row[1],
                target_role_id=row[2],
                max_users=row[3],
                id=row[0],
                last_rotation=row[5],
                remove_when_offline=row[6],
                prioritize_active=row[7],
                ignore_timed_out=row[8],
                blacklisted_role_id=row[9],
                always_replace_current=row[10],
                blacklisted_role_id_2=row[11],
                blacklisted_role_id_3=row[12],
                blacklisted_role_id_4=row[13]
            )
            config.rotation_interval_hours = row[4] or 1  # Default to 1 if None
            configs.append(config)
        
        if not configs:
            await interaction.followup.send("❌ No spotlight configurations found for this server.", ephemeral=True)
            return
            
        # Get the maximum allowed configs for the guild
        max_configs = await self.get_guild_max_configs(interaction.guild_id)
        current_configs = len(configs)
        
        # Create embed
        embed = discord.Embed(
            title=f"Spotlight Configurations for {interaction.guild.name}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Configurations: {current_configs}/{max_configs}")
        
        current_time = datetime.now(timezone.utc)
        
        for i, config in enumerate(configs, 1):
            initial_role = interaction.guild.get_role(config.initial_role_id)
            target_role = interaction.guild.get_role(config.target_role_id)
            
            initial_role_name = initial_role.mention if initial_role else f"<@&{config.initial_role_id}> (Deleted)"
            target_role_name = target_role.mention if target_role else f"<@&{config.target_role_id}> (Deleted)"
            
            blacklisted_role = interaction.guild.get_role(config.blacklisted_role_id)
            blacklisted_role_name = blacklisted_role.mention if blacklisted_role else f"<@&{config.blacklisted_role_id}> (Deleted)"

            blacklisted_role_2 = interaction.guild.get_role(config.blacklisted_role_id_2)
            blacklisted_role_2_name = blacklisted_role_2.mention if blacklisted_role_2 else f"<@&{config.blacklisted_role_id_2}> (Deleted)"

            blacklisted_role_3 = interaction.guild.get_role(config.blacklisted_role_id_3)
            blacklisted_role_3_name = blacklisted_role_3.mention if blacklisted_role_3 else f"<@&{config.blacklisted_role_id_3}> (Deleted)"

            blacklisted_role_4 = interaction.guild.get_role(config.blacklisted_role_id_4)
            blacklisted_role_4_name = blacklisted_role_4.mention if blacklisted_role_4 else f"<@&{config.blacklisted_role_id_4}> (Deleted)"
            
            # Format rotation interval
            rotation_interval = config.rotation_interval_hours or 1
            if rotation_interval == 5:
                interval_str = "1 minute (Debug)"
            else:
                interval_str = f"{int(rotation_interval)} hour{'s' if rotation_interval != 1 else ''}"
            
            if config.last_rotation:
                last_rotation_dt = config.last_rotation
                if isinstance(last_rotation_dt, str):
                    last_rotation_dt = datetime.fromisoformat(last_rotation_dt)
                
                est = pytz.timezone('US/Eastern')
                last_rotation_dt = last_rotation_dt.astimezone(est)
                
                current_time_est = datetime.now(est)
                if last_rotation_dt > current_time_est:
                    next_rotation_dt = last_rotation_dt
                    last_rotation = "*Not rotated yet*"
                    next_rotation = f"🟢 <t:{int(next_rotation_dt.timestamp())}:R>"
                else:
                    last_rotation = f"<t:{int(last_rotation_dt.timestamp())}:R>"
                    
                    if rotation_interval == 5:  # Debug mode - 1 minute
                        next_rotation_dt = last_rotation_dt + timedelta(minutes=1)
                    else:
                        next_rotation_dt = last_rotation_dt + timedelta(hours=rotation_interval)
                    
                    next_rotation = f"<t:{int(next_rotation_dt.timestamp())}:R>"
                    if next_rotation_dt < current_time_est:
                        next_rotation = f"🔴 {next_rotation} (Overdue!)"
                    else:
                        next_rotation = f"🟢 {next_rotation}"
            else:
                last_rotation = "*Not rotated yet*"
            
            embed.add_field(
                name=f"Configuration #{i}",
                value=(
                    f"• **Initial Role:** {initial_role_name}\n"
                    f"• **Target Role:** {target_role_name}\n"
                    f"• **Max Users:** {config.max_users}\n"
                    f"• **Rotation Interval:** {interval_str}\n"
                    f"• **Last Rotation:** {last_rotation}\n"
                    f"• **Next Rotation:** {next_rotation}\n"
                    + (f"• **Remove When Offline:** `True`\n" if config.remove_when_offline else '')
                    + (f"• **Prioritize Active:** `True`\n" if config.prioritize_active else '')
                    + (f"• **Ignore Timed Out:** `True`\n" if config.ignore_timed_out else '')
                    + (f"• **Always Replace Current:** `True`\n" if config.always_replace_current else '')
                    + (f"• **Blacklisted Role 1:** {blacklisted_role_name}\n" if blacklisted_role else '')
                    + (f"• **Blacklisted Role 2:** {blacklisted_role_2_name}\n" if blacklisted_role_2 else '')
                    + (f"• **Blacklisted Role 3:** {blacklisted_role_3_name}\n" if blacklisted_role_3 else '')
                    + (f"• **Blacklisted Role 4:** {blacklisted_role_4_name}\n" if blacklisted_role_4 else '')
                ),
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
        
    @app_commands.command(name="remove")
    @app_commands.describe(
        config="The configuration to remove"
    )
    @app_commands.autocomplete(config=config_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_spotlight(
        self,
        interaction: discord.Interaction,
        config: str
    ):
        """Remove a spotlight configuration"""
        # Extract config ID from the autocomplete string (format: "ID: Initial Role → Target Role")
        try:
            config_id = int(config.split(':', 1)[0].strip())
        except (ValueError, IndexError):
            await interaction.response.send_message(
                "❌ Invalid configuration format. Please select a configuration from the autocomplete list.",
                ephemeral=True
            )
            return

        # Check if the config exists and belongs to this guild
        cursor = self.db.cursor()
        cursor.execute(
            'SELECT id, initial_role_id, target_role_id FROM spotlight WHERE id = ? AND guild_id = ?',
            (config_id, interaction.guild_id)
        )
        
        config_data = cursor.fetchone()
        if not config_data:
            await interaction.response.send_message(
                "❌ Configuration not found or you don't have permission to remove it.",
                ephemeral=True
            )
            return
            
        # Get role objects for logging
        initial_role = interaction.guild.get_role(config_data[1])
        target_role = interaction.guild.get_role(config_data[2])
        
        # Delete the configuration
        cursor.execute('DELETE FROM spotlight WHERE id = ?', (config_id,))
        self.db.commit()
        await self.cache.delete(interaction.guild_id)
        
        # Queue role removal for all members with the target role
        if target_role:
            for member in target_role.members:
                await self.queue_role_operation(member, target_role, False)
        
        # Prepare response message
        initial_role_mention = initial_role.mention if initial_role else f"<@&{config_data[1]}> (Deleted)"
        target_role_mention = target_role.mention if target_role else f"<@&{config_data[2]}> (Deleted)"
        
        await interaction.response.send_message(
            f"✅ Removed spotlight configuration:\n"
            f"• Initial role: {initial_role_mention}\n"
            f"• Target role: {target_role_mention}",
            allowed_mentions=discord.AllowedMentions.none(),
            ephemeral=True
        )

    @app_commands.command(name="time")
    @app_commands.describe(
        time="Time in 12-hour (e.g., '2:30 PM') or 24-hour format (e.g., '14:30')",
        date="Optional: Specific date (e.g., '2025-06-15' or 'tomorrow' or 'next week')",
        config="The spotlight configuration to set the time for"
    )
    @app_commands.autocomplete(config=config_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def set_spotlight_time(
        self,
        interaction: discord.Interaction,
        time: str,
        date: str = None,
        config: str = None,
    ):
        """Set the rotation time for spotlight configurations in EST timezone.

        Parameters
        ----------
        interaction : discord.Interaction
            The interaction that triggered this command
        time : str
            Time in 12-hour (e.g., '2:30 PM') or 24-hour format (e.g., '14:30')
        config : Optional[str]
            The configuration to update (format: 'ID: Initial Role → Target Role')
        """
        try:
            # Set the timezone to EST
            est = pytz.timezone('US/Eastern')
            
            # First try to parse with dateutil.parser for maximum flexibility
            try:
                from dateutil import parser
                dt = parser.parse(time, fuzzy=True)
                time_obj = dt.time()
            except (ImportError, ValueError):
                # Fallback to manual parsing if dateutil is not available
                time_lower = time.lower().strip()
                
                # Initialize default values
                hours = 0
                minutes = 0
                
                # Check for AM/PM
                if 'am' in time_lower or 'pm' in time_lower:
                    # Extract period and clean time part
                    if 'am' in time_lower:
                        period = 'am'
                        time_part = time_lower.replace('am', '').strip()
                    else:
                        period = 'pm'
                        time_part = time_lower.replace('pm', '').strip()
                    
                    # Parse hours and minutes
                    if ':' in time_part:
                        hours, minutes = map(int, time_part.split(':'))
                    else:
                        hours = int(time_part)
                    
                    # Convert to 24-hour format
                    if period == 'pm' and hours < 12:
                        hours += 12
                    elif period == 'am' and hours == 12:
                        hours = 0
                else:
                    # Parse as 24-hour format
                    if ':' in time:
                        hours, minutes = map(int, time.split(':'))
                    else:
                        hours = int(time)
                
                # Validate hours and minutes
                if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                    raise ValueError("Invalid time values")
                    
                time_obj = datetime.strptime(f"{hours:02d}:{minutes:02d}", '%H:%M').time()
            
            # Parse date if provided
            if date is not None:
                try:
                    now_est = datetime.now(est)
                    
                    # Handle relative dates
                    date_lower = date.lower()
                    if date_lower == 'tomorrow':
                        target_date = now_est.date() + timedelta(days=1)
                    elif date_lower == 'next week':
                        target_date = now_est.date() + timedelta(weeks=1)
                    else:
                        # Try to parse as YYYY-MM-DD or MM/DD/YYYY
                        try:
                            # Parse as UTC first, then convert to EST
                            parsed_date = datetime.strptime(date, '%Y-%m-%d')
                            target_date = parsed_date.astimezone(est).date()
                        except ValueError:
                            try:
                                parsed_date = datetime.strptime(date, '%m/%d/%Y')
                                target_date = parsed_date.astimezone(est).date()
                            except ValueError:
                                raise ValueError("Invalid date format. Use YYYY-MM-DD or MM/DD/YYYY")
                    
                    # Create datetime for the specified date and time in EST
                    naive_dt = datetime.combine(target_date, time_obj)
                    next_rotation = est.localize(naive_dt)
                    
                    # Allow past dates - they'll be treated as the next occurrence
                    pass
                        
                except ValueError as e:
                    await interaction.response.send_message(
                        f"❌ Error: {str(e)}. Please use a valid date format (YYYY-MM-DD or MM/DD/YYYY) "
                        "or a relative date like 'tomorrow' or 'next week'.",
                        ephemeral=True
                    )
                    return
            else:
                # Get current time in EST
                now_est = datetime.now(est)
                
                # Create a datetime with today's date and the specified time in EST
                target_date = now_est.date()
                naive_dt = datetime.combine(target_date, time_obj)
                next_rotation = est.localize(naive_dt)
                
            # Convert to UTC for storage
            next_rotation_utc = next_rotation.astimezone(timezone.utc)
            
        except ValueError as e:
            await interaction.response.send_message(
                "❌ Error: Invalid time format. Please use either 12-hour (e.g., '2:30 PM') or 24-hour format (e.g., '14:30').",
                ephemeral=True
            )
            return
            
        # If we get here, time was parsed successfully
        cursor = self.db.cursor()
        
        # Update the database
        if config:
            # Update a specific config
            config_id = int(config.split(':')[0])
            cursor.execute('''
                UPDATE spotlight 
                SET last_rotation = ? 
                WHERE id = ? AND guild_id = ?
            ''', (next_rotation_utc.isoformat(), config_id, interaction.guild_id))
            
            if cursor.rowcount == 0:
                await interaction.response.send_message(
                    "❌ Error: Could not find the specified configuration.",
                    ephemeral=True
                )
                return
        else:
            # Update all configs for this guild
            cursor.execute('''
                UPDATE spotlight 
                SET last_rotation = ? 
                WHERE guild_id = ?
            ''', (next_rotation_utc.isoformat(), interaction.guild_id))
        
        self.db.commit()
        
        # Invalidate cache
        await self.cache.delete(interaction.guild_id)
        
        # Format the date and time for display
        display_date = next_rotation.strftime('%B %d, %Y')  # e.g., 'June 15, 2025'
        display_hour = next_rotation.hour % 12 or 12  # Convert 0 to 12 for 12-hour format
        am_pm = 'AM' if next_rotation.hour < 12 else 'PM'
        display_time = f"{display_date} at {display_hour}:{next_rotation.minute:02d} {am_pm}"
        
        # Add config info if provided
        config_info = f" for config: {config}" if config else " for all configurations"
        
        # Format the next rotation time for display using Discord's timestamp
        next_rotation_str = f"<t:{int(next_rotation.timestamp())}:F>"
        
        await interaction.response.send_message(
            f"✅ Spotlight time updated{config_info} to {display_time} EST\n"
            f"Next rotation will be at: {next_rotation_str}",
            ephemeral=True
        )
        
        logger.info(
            f"Spotlight time updated by {interaction.user} (ID: {interaction.user.id}) with: "
            f"time={display_time} EST "
            f"(next rotation: {next_rotation_utc.isoformat(timespec='seconds')} UTC)"
        )
        
    async def queue_role_operation(self, member: discord.Member, role: discord.Role, add: bool):
        """Add a role operation to the queue"""
        async with self.role_queue_lock:
            self.role_queue.append(RoleOperation(member, role, add))

    async def process_role_queue(self):
        """Process the role operation queue with rate limiting"""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                # Process one operation at a time with a small delay
                operation = None
                async with self.role_queue_lock:
                    if self.role_queue:
                        operation = self.role_queue.popleft()
                
                if operation:
                    try:
                        if operation.add:
                            await operation.member.add_roles(
                                operation.role, 
                                reason="Spotlight rotation"
                            )
                        else:
                            await operation.member.remove_roles(
                                operation.role,
                                reason="Spotlight rotation"
                            )
                        # Small delay between operations to avoid rate limits
                        await asyncio.sleep(0.5)
                    except discord.HTTPException as e:
                        operation.attempts += 1
                        operation.last_attempt = datetime.utcnow()
                        
                        if operation.attempts < 3:  # Retry up to 3 times
                            logger.warning(
                                f"Failed to update role for {operation.member} (attempt {operation.attempts}): {e}"
                            )
                            async with self.role_queue_lock:
                                self.role_queue.appendleft(operation)
                            # Longer delay after a failure
                            await asyncio.sleep(5)
                        else:
                            logger.error(
                                f"Failed to update role for {operation.member} after 3 attempts: {e}"
                            )
                    except Exception as e:
                        logger.error(f"Unexpected error processing role operation: {e}")
                        await asyncio.sleep(5)  # Prevent tight loop on unexpected errors
                else:
                    # No operations to process, sleep briefly
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error in role queue processor: {e}", exc_info=True)
                await asyncio.sleep(5)  # Prevent tight loop on errors
            
    @tasks.loop(seconds=10, reconnect=True)  # Run every 10 seconds to check for configs to process
    async def rotate_spotlight(self):
        """Rotate spotlight users for configurations that are due for rotation"""
        cursor = self.db.cursor()
        current_time = datetime.now(timezone.utc)
        
        logger.debug(f"[ROTATION] Starting rotation check at {current_time.isoformat()}")
        
        # Get all configurations
        cursor.execute('''
            SELECT id, guild_id, initial_role_id, target_role_id, max_users,
                COALESCE(rotation_interval_hours, 1) as rotation_interval_hours,
                last_rotation,
                prioritize_active,
                ignore_timed_out,
                blacklisted_role_id,
                always_replace_current,
                blacklisted_role_id_2,
                blacklisted_role_id_3,
                blacklisted_role_id_4
            FROM spotlight
            WHERE guild_id IS NOT NULL
        ''')

        all_configs = cursor.fetchall()
        logger.debug(f"[ROTATION] Found {len(all_configs)} total configurations")
        
        due_configs = []
        
        # Determine which configs are due for rotation
        for row in all_configs:
            config_id, guild_id, initial_role_id, target_role_id, max_users, interval, last_rotation, prioritize_active, ignore_timed_out, blacklisted_role_id, always_replace_current, blacklisted_role_id_2, blacklisted_role_id_3, blacklisted_role_id_4 = row
            
            logger.debug(f"[ROTATION] Checking config {config_id} (Guild: {guild_id})")
            logger.debug(f"[ROTATION] Config {config_id}: last_rotation={last_rotation}, interval={interval}h")
            
            # First rotation - no last_rotation timestamp
            if last_rotation is None:
                due_configs.append(row)
                logger.debug(f"[ROTATION] Config {config_id} never rotated, adding to due list")
                continue
            
            try:
                # Normalize last_rotation to datetime with timezone
                if isinstance(last_rotation, str):
                    last_rotation_dt = datetime.fromisoformat(last_rotation)
                else:
                    last_rotation_dt = last_rotation
                
                # Ensure timezone is set
                if last_rotation_dt.tzinfo is None:
                    last_rotation_dt = last_rotation_dt.replace(tzinfo=timezone.utc)
                    logger.debug(f"[ROTATION] Config {config_id}: Added UTC timezone to last_rotation")
                
                # Calculate next rotation time
                if interval == 5:  # Debug mode: 1 minute intervals
                    next_rotation_dt = last_rotation_dt + timedelta(minutes=1)
                    logger.debug(f"[ROTATION] Config {config_id}: Debug mode - 1 minute interval")
                else:
                    next_rotation_dt = last_rotation_dt + timedelta(hours=interval)
                
                logger.debug(f"[ROTATION] Config {config_id}: last_rotation={last_rotation_dt.isoformat()}, next_rotation={next_rotation_dt.isoformat()}, current={current_time.isoformat()}")
                
                # Check if rotation is due
                if current_time >= next_rotation_dt:
                    due_configs.append(row)
                    logger.info(f"[ROTATION] Config {config_id} is DUE for rotation")
                else:
                    time_until_next = next_rotation_dt - current_time
                    logger.debug(f"[ROTATION] Config {config_id} not due - {time_until_next} until next rotation")
                    
            except Exception as e:
                logger.error(f"[ROTATION] Error processing due check for config {config_id}: {e}", exc_info=True)
                logger.error(f"[ROTATION] Config {config_id}: last_rotation raw value: {repr(last_rotation)}")
                # Skip this config - don't add to due_configs if we can't parse the date
                continue
        
        # Sort due configs by last_rotation (None first, then by date)
        due_configs.sort(key=lambda x: (x[6] is not None, x[6] or datetime.min))
        
        # logger.info(f"[ROTATION] Found {len(due_configs)} configurations due for rotation")
        
        if not due_configs:
            logger.debug("[ROTATION] No configurations due for rotation")
            return
        
        # Process each due configuration
        for row in due_configs:
            config_id, guild_id, initial_role_id, target_role_id, max_users, rotation_interval, last_rotation, prioritize_active, ignore_timed_out, blacklisted_role_id, always_replace_current, blacklisted_role_id_2, blacklisted_role_id_3, blacklisted_role_id_4 = row
            
            logger.info(f"[ROTATION] PROCESSING due config {config_id} (Guild: {guild_id})")
            
            # Calculate expected rotation time for logging
            if last_rotation:
                try:
                    if isinstance(last_rotation, str):
                        last_rotation_dt = datetime.fromisoformat(last_rotation)
                    else:
                        last_rotation_dt = last_rotation
                    
                    if last_rotation_dt.tzinfo is None:
                        last_rotation_dt = last_rotation_dt.replace(tzinfo=timezone.utc)
                    
                    next_rotation = last_rotation_dt + timedelta(hours=rotation_interval)
                    logger.debug(f"[ROTATION] Config {config_id}: Expected rotation time was {next_rotation.isoformat()}")
                except Exception as e:
                    logger.error(f"[ROTATION] Error calculating expected rotation time for config {config_id}: {e}")
            else:
                logger.debug(f"[ROTATION] Config {config_id}: First rotation for new configuration")
            
            # Begin transaction for this config
            try:
                # Get guild and validate
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    logger.warning(f"[ROTATION] Guild {guild_id} not found for config {config_id} - removing configuration")
                    cursor.execute('DELETE FROM spotlight WHERE id = ?', (config_id,))
                    self.db.commit()
                    logger.info(f"[ROTATION] Removed configuration {config_id} for guild {guild_id} (bot not in guild)")
                    continue
                
                # Get roles and validate
                initial_role = guild.get_role(initial_role_id)
                target_role = guild.get_role(target_role_id)
                
                if not initial_role:
                    logger.error(f"[ROTATION] Initial role {initial_role_id} not found in guild {guild_id}")
                    cursor.execute('DELETE FROM spotlight WHERE id = ?', (config_id,))
                    self.db.commit()
                    logger.info(f"[ROTATION] Removed configuration {config_id} for guild {guild_id} (initial role not found)")
                    continue
                
                if not target_role:
                    logger.error(f"[ROTATION] Target role {target_role_id} not found in guild {guild_id}")
                    cursor.execute('DELETE FROM spotlight WHERE id = ?', (config_id,))
                    self.db.commit()
                    logger.info(f"[ROTATION] Removed configuration {config_id} for guild {guild_id} (target role not found)")
                    continue
                
                # Get blacklisted roles
                blacklisted_roles = []
                for role_id in [blacklisted_role_id, blacklisted_role_id_2, 
                              blacklisted_role_id_3, blacklisted_role_id_4]:
                    if role_id:
                        role = guild.get_role(role_id)
                        if role:
                            blacklisted_roles.append(role)
                
                logger.debug(f"[ROTATION] Config {config_id}: Found {len(blacklisted_roles)} blacklisted roles")
                
                # Get current spotlight members
                current_spotlight = []
                for member in guild.members:
                    try:
                        if target_role in member.roles and initial_role in member.roles:
                            current_spotlight.append(member)
                    except Exception as e:
                        logger.warning(f"[ROTATION] Error checking current spotlight member {member.id}: {e}")
                        continue
                
                logger.debug(f"[ROTATION] Config {config_id}: Found {len(current_spotlight)} current spotlight members")
                
                # Get all eligible members
                all_eligible = []
                for member in guild.members:
                    try:
                        if (initial_role in member.roles and 
                            not any(role in member.roles for role in blacklisted_roles)):
                            all_eligible.append(member)
                    except Exception as e:
                        logger.warning(f"[ROTATION] Error processing member {member.id}: {e}")
                        continue
                
                logger.debug(f"[ROTATION] Config {config_id}: Found {len(all_eligible)} eligible members after filtering")
                
                if not all_eligible:
                    logger.warning(f"[ROTATION] No eligible members found for config {config_id}")
                    continue
                
                # Filter members based on activity and timeout status
                active_members = []
                offline_members = []
                
                for member in all_eligible:
                    try:
                        # Check if member is timed out
                        is_timed_out = getattr(member, 'timed_out_until', None) is not None
                        if ignore_timed_out and is_timed_out:
                            logger.debug(f"[ROTATION] Skipping timed out member {member.display_name}")
                            continue
                        
                        # Check online status if prioritizing active members
                        if prioritize_active:
                            is_offline = (member.status == discord.Status.offline or 
                                        member.status == discord.Status.invisible)
                            if is_offline:
                                offline_members.append(member)
                            else:
                                active_members.append(member)
                        else:
                            active_members.append(member)
                            
                    except Exception as e:
                        logger.warning(f"[ROTATION] Error checking member {member.id} status: {e}")
                        continue
                
                logger.debug(f"[ROTATION] Config {config_id}: {len(active_members)} active members, {len(offline_members)} offline members")
                
                # Shuffle the member lists for randomization
                random.shuffle(active_members)
                random.shuffle(offline_members)
                
                # Select new spotlight members
                selected_members = []
                selected_members.extend(active_members[:max_users])
                remaining_slots = max_users - len(selected_members)
                
                if remaining_slots > 0 and offline_members:
                    selected_members.extend(offline_members[:remaining_slots])
                
                logger.debug(f"[ROTATION] Config {config_id}: Selected {len(selected_members)} members for spotlight")
                
                # Track role operations
                removal_count = 0
                addition_count = 0
                role_operation_errors = []
                
                # Handle role changes based on replacement strategy
                if always_replace_current:
                    # Always replace current spotlight members
                    logger.debug(f"[ROTATION] Config {config_id}: Using always_replace_current strategy")
                    
                    # Get eligible replacements (not currently in spotlight)
                    eligible_replacements = []
                    for member in all_eligible:
                        try:
                            if member not in current_spotlight:
                                # Double-check timeout status
                                is_timed_out = getattr(member, 'timed_out_until', None) is not None
                                if not ignore_timed_out or not is_timed_out:
                                    eligible_replacements.append(member)
                        except Exception as e:
                            logger.warning(f"[ROTATION] Error checking member {member.id} for replacement: {e}")
                            continue
                    
                    # Sort replacements by activity if needed
                    if prioritize_active:
                        active_replacements = []
                        offline_replacements = []
                        
                        for member in eligible_replacements:
                            try:
                                is_offline = (member.status == discord.Status.offline or 
                                            member.status == discord.Status.invisible)
                                if is_offline:
                                    offline_replacements.append(member)
                                else:
                                    active_replacements.append(member)
                            except Exception as e:
                                logger.warning(f"[ROTATION] Error checking member {member.id} activity: {e}")
                                continue
                        
                        random.shuffle(active_replacements)
                        random.shuffle(offline_replacements)
                        eligible_replacements = active_replacements + offline_replacements
                    else:
                        random.shuffle(eligible_replacements)
                    
                    # If no current members, add all eligible replacements
                    if not current_spotlight:
                        logger.debug(f"[ROTATION] Config {config_id}: No current spotlight members, adding all eligible replacements")
                        selected_members = eligible_replacements[:max_users]
                        for member in selected_members:
                            try:
                                await self.queue_role_operation(member, target_role, True)
                                addition_count += 1
                                logger.debug(f"[ROTATION] Queued addition of {target_role.name} to {member.display_name}")
                            except Exception as e:
                                logger.error(f"[ROTATION] Error adding role to {member.display_name}: {e}")
                                role_operation_errors.append(f"Add role to {member.display_name}: {e}")
                    else:
                        # Replace current spotlight members
                        if current_spotlight and eligible_replacements:
                            num_to_replace = min(len(current_spotlight), len(eligible_replacements))
                            
                            if num_to_replace > 0:
                                users_to_remove = random.sample(current_spotlight, num_to_replace)
                                users_to_add = eligible_replacements[:num_to_replace]
                                
                                # Remove roles from selected current members
                                for member in users_to_remove:
                                    try:
                                        await self.queue_role_operation(member, target_role, False)
                                        removal_count += 1
                                        logger.debug(f"[ROTATION] Queued removal of {target_role.name} from {member.display_name}")
                                    except Exception as e:
                                        logger.error(f"[ROTATION] Error removing role from {member.display_name}: {e}")
                                        role_operation_errors.append(f"Remove role from {member.display_name}: {e}")
                                
                                # Add roles to replacement members
                                for member in users_to_add:
                                    try:
                                        await self.queue_role_operation(member, target_role, True)
                                        addition_count += 1
                                        logger.debug(f"[ROTATION] Queued addition of {target_role.name} to {member.display_name}")
                                    except Exception as e:
                                        logger.error(f"[ROTATION] Error adding role to {member.display_name}: {e}")
                                        role_operation_errors.append(f"Add role to {member.display_name}: {e}")
                                
                                logger.debug(f"[ROTATION] Config {config_id}: Replaced {num_to_replace} spotlight members")
                                
                                # Fill remaining slots if any
                                current_spotlight_after_replacement = len(current_spotlight) - len(users_to_remove) + len(users_to_add)
                                remaining_slots = max(0, max_users - current_spotlight_after_replacement)
                                
                                if remaining_slots > 0:
                                    available_replacements = [m for m in eligible_replacements if m not in users_to_add]
                                    additional_members = available_replacements[:remaining_slots]
                                    
                                    for member in additional_members:
                                        try:
                                            await self.queue_role_operation(member, target_role, True)
                                            addition_count += 1
                                            logger.debug(f"[ROTATION] Queued addition of {target_role.name} to {member.display_name} (filling slot)")
                                        except Exception as e:
                                            logger.error(f"[ROTATION] Error adding role to {member.display_name}: {e}")
                                            role_operation_errors.append(f"Add role to {member.display_name}: {e}")
                                
                                    logger.debug(f"[ROTATION] Config {config_id}: Added {len(additional_members)} additional members")
                        else:
                            logger.debug(f"[ROTATION] Config {config_id}: No members to replace")
                    
                else:
                    # Standard rotation - remove non-selected, add selected
                    logger.debug(f"[ROTATION] Config {config_id}: Using standard rotation strategy")
                    
                    # Remove role from current spotlight members not in selected list
                    for member in current_spotlight:
                        if member not in selected_members:
                            try:
                                await self.queue_role_operation(member, target_role, False)
                                removal_count += 1
                                logger.debug(f"[ROTATION] Queued removal of {target_role.name} from {member.display_name}")
                            except Exception as e:
                                logger.error(f"[ROTATION] Error removing role from {member.display_name}: {e}")
                                role_operation_errors.append(f"Remove role from {member.display_name}: {e}")
                    
                    # Add role to selected members who don't have it
                    for member in selected_members:
                        try:
                            if target_role not in member.roles:
                                await self.queue_role_operation(member, target_role, True)
                                addition_count += 1
                                logger.debug(f"[ROTATION] Queued addition of {target_role.name} to {member.display_name}")
                        except Exception as e:
                            logger.error(f"[ROTATION] Error adding role to {member.display_name}: {e}")
                            role_operation_errors.append(f"Add role to {member.display_name}: {e}")
                
                logger.info(f"[ROTATION] Config {config_id}: Queued {removal_count} removals and {addition_count} additions")
                
                # Update last rotation only if no role operation errors
                if not role_operation_errors:
                    # Calculate the scheduled rotation time
                    rotation_time = current_time
                    
                    # If we have a previous rotation, calculate the proper next rotation time
                    if last_rotation:
                        try:
                            if isinstance(last_rotation, str):
                                last_rotation_dt = datetime.fromisoformat(last_rotation)
                            else:
                                last_rotation_dt = last_rotation
                            
                            if last_rotation_dt.tzinfo is None:
                                last_rotation_dt = last_rotation_dt.replace(tzinfo=timezone.utc)
                            
                            # Calculate what the rotation time should be based on the interval
                            expected_rotation = last_rotation_dt
                            while expected_rotation <= current_time:
                                expected_rotation += timedelta(hours=rotation_interval)
                            
                            # Use the last valid rotation time (one interval before expected_rotation)
                            rotation_time = expected_rotation - timedelta(hours=rotation_interval)
                            
                            # But don't set it to a future time
                            if rotation_time > current_time:
                                rotation_time = current_time
                                
                        except Exception as e:
                            logger.error(f"[ROTATION] Error calculating rotation time for config {config_id}: {e}")
                            rotation_time = current_time
                    
                    # Update the database
                    logger.debug(f"[ROTATION] Config {config_id}: Updating last_rotation to {rotation_time.isoformat()}")
                    cursor.execute(
                        'UPDATE spotlight SET last_rotation = ? WHERE id = ?',
                        (rotation_time.isoformat(), config_id)
                    )
                    self.db.commit()
                    
                    # Clear cache
                    await self.cache.delete(guild_id)
                    
                    # Calculate and log next rotation time
                    next_rotation_time = rotation_time + timedelta(hours=rotation_interval)
                    logger.info(f"[ROTATION] Successfully completed rotation for config {config_id} (Guild: {guild_id})")
                    logger.info(f"[ROTATION] Config {config_id}: Next rotation scheduled for {next_rotation_time.isoformat()}")
                    
                else:
                    logger.error(f"[ROTATION] Config {config_id}: Role operations failed. Errors: {'; '.join(role_operation_errors)}")
                    logger.error(f"[ROTATION] Config {config_id}: Not updating last_rotation - will retry on next interval")
                    
            except Exception as e:
                logger.error(f"[ROTATION] Error processing config {config_id}: {e}", exc_info=True)
                try:
                    self.db.rollback()
                except Exception as rollback_error:
                    logger.error(f"[ROTATION] Error rolling back transaction for config {config_id}: {rollback_error}")
                
                logger.error(f"[ROTATION] Config {config_id}: Failed to complete rotation, will retry on next interval")
        
        logger.info(f"[ROTATION] Completed rotation check - processed {len(due_configs)} configurations")
    
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Handle member updates to rotate out users who go offline"""
        
        if before.status == after.status:
            return
            
        if after.status != discord.Status.offline:
            return
            
            
        # Get all spotlight configurations for this guild with all necessary fields
        cursor = self.db.cursor()
        cursor.execute(
            '''SELECT id, initial_role_id, target_role_id, rotation_interval_hours, 
                      remove_when_offline, ignore_timed_out, prioritize_active,
                      blacklisted_role_id, blacklisted_role_id_2, 
                      blacklisted_role_id_3, blacklisted_role_id_4
               FROM spotlight 
               WHERE guild_id = ?''',
            (after.guild.id,)
        )
        
        # Find all target roles the member has
        member_target_roles = []
        for row in cursor.fetchall():
            (config_id, initial_role_id, target_role_id, rotation_interval, 
             remove_when_offline, ignore_timed_out, prioritize_active,
             blacklisted_role_id, blacklisted_role_id_2, 
             blacklisted_role_id_3, blacklisted_role_id_4) = row
            
            # Skip if remove_when_offline is False for this config
            if not remove_when_offline:
                continue
                
            target_role = after.guild.get_role(target_role_id)
            if target_role and target_role in after.roles:
                initial_role = after.guild.get_role(initial_role_id)
                if initial_role:
                    # Get all blacklisted roles for this config
                    blacklisted_roles = []
                    for role_id in [blacklisted_role_id, blacklisted_role_id_2, 
                                  blacklisted_role_id_3, blacklisted_role_id_4]:
                        if role_id:
                            role = after.guild.get_role(role_id)
                            if role:
                                blacklisted_roles.append(role)
                    
                    member_target_roles.append((
                        initial_role, 
                        target_role, 
                        rotation_interval,
                        ignore_timed_out,
                        prioritize_active,
                        blacklisted_roles
                    ))
        
        # Process each target role the member has
        for config_data in member_target_roles:
            if len(config_data) == 3:  # Backward compatibility
                initial_role, target_role, rotation_interval = config_data
                ignore_timed_out = False
                prioritize_active = False
                blacklisted_roles = []
            else:
                (initial_role, target_role, rotation_interval, 
                 ignore_timed_out, prioritize_active, blacklisted_roles) = config_data
                   
            # Base query for potential replacements
            potential_members = [m for m in after.guild.members 
                              if m.id != after.id  # Don't select the current member
                              and initial_role in m.roles 
                              and target_role not in m.roles
                              and not m.bot  # Exclude bots
                              and not any(role in m.roles for role in blacklisted_roles)]  # Exclude blacklisted roles
            
            # Initialize active_members for logging purposes
            active_members = []
            
            # Filter by online status and timeout based on config
            if prioritize_active:
                # In prioritize_active mode, we prefer online members but will fall back to offline if needed
                active_members = [m for m in potential_members 
                                if m.status != discord.Status.offline 
                                and m.status != discord.Status.invisible
                                and (not ignore_timed_out or not m.timed_out_until)]
                
                offline_members = [m for m in potential_members 
                                 if (m.status == discord.Status.offline or m.status == discord.Status.invisible)
                                 and (not ignore_timed_out or not m.timed_out_until)]
                
                # Shuffle both lists for random selection within each group
                random.shuffle(active_members)
                random.shuffle(offline_members)
                
                # Try to select from active members first, fall back to offline if needed
                eligible_members = active_members if active_members else offline_members
            else:
                # In non-prioritize mode, just select from all potential members
                eligible_members = [m for m in potential_members
                                    if not ignore_timed_out or not m.timed_out_until]
                                  
            if eligible_members:
                # Only remove the role if we found a replacement
                await self.queue_role_operation(after, target_role, False)
                
                # Select a random eligible member
                replacement = random.choice(eligible_members)
                await self.queue_role_operation(replacement, target_role, True)
            else:
                pass
    
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """Handle role deletion to remove affected configurations"""
        cursor = self.db.cursor()
        
        # Find all configurations that use the deleted role
        cursor.execute(
            'SELECT id, guild_id, initial_role_id, target_role_id FROM spotlight '
            'WHERE guild_id = ? AND (initial_role_id = ? OR target_role_id = ?)',
            (role.guild.id, role.id, role.id)
        )
        
        affected_configs = cursor.fetchall()
        
        for config_id, guild_id, initial_role_id, target_role_id in affected_configs:
            # Delete the affected configuration
            cursor.execute('DELETE FROM spotlight WHERE id = ?', (config_id,))
            
            # Invalidate cache for this guild
            await self.cache.delete(guild_id)
            
            # Notify server admins about the issue
            guild = self.bot.get_guild(guild_id)
            if guild:
                # Try to find a channel to send the notification
                channel = guild.system_channel or next(
                    (c for c in guild.text_channels if c.permissions_for(guild.me).send_messages),
                    None
                )
                
                if channel:
                    try:
                        await channel.send(
                            f"⚠️ A spotlight configuration was removed because the role <@&{role.id}> was deleted. "
                            f"You can configure a new spotlight with `/spotlight set`."
                        )
                    except Exception as e:
                        logger.error(f"Failed to send role deletion notification in {guild_id}: {e}")
        
        self.db.commit()

class SpotlightCommands(commands.Cog):
    def __init__(self, bot, spotlight_instance):
        self.bot = bot
        self.spotlight = spotlight_instance
        
    # these are user install commands, so they're usable globally but require you install the app as a user install to see them
    # specifically if you're using your own custom bot these features allow you to modify the amount of spotlight configs a guild can have
    
    @app_commands.command(name="smc", description="Set the maximum number of spotlight configs for a guild")
    @app_commands.describe(
        guild_id="The ID of the guild to update",
        amount="Maximum number of spotlight configurations (minimum 2)"
    )
    @app_commands.allowed_installs(users=True)
    @app_commands.allowed_contexts(guilds=True)
    async def set_max_configs(self, interaction: discord.Interaction, guild_id: str, amount: int):
    
        if interaction.user.id != 311456723682590721: # if you're using your own custom bot with this feature replace the user ID with your own
            await interaction.response.send_message("❌ You are not authorized to use this command.", ephemeral=True)
            return # alternatively you can simply delete this if statement on your own custom bot

        try:
            guild_id_int = int(guild_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid guild ID. Please provide a valid numeric ID.", ephemeral=True)
            return
            
        if amount < 2:
            await interaction.response.send_message("❌ Maximum configurations must be at least the default of 2.", ephemeral=True)
            return
            
        await self.spotlight.set_guild_max_configs(guild_id_int, amount)
        await interaction.response.send_message(f"✅ Set maximum spotlight configurations to {amount} for guild {guild_id}", ephemeral=True)

        logger.info(f"Set maximum spotlight configurations to {amount} for guild {guild_id}")

async def setup(bot):
    spotlight = Spotlight(bot)
    await bot.add_cog(spotlight)
    await bot.add_cog(SpotlightCommands(bot, spotlight))