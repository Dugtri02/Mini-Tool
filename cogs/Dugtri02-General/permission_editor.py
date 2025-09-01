# Use these commands to mass edit channel and role permissions

import discord; from discord import app_commands, File; from discord.ext import commands
from typing import Dict, List, Optional, Union, Any, Tuple
import time, json, io; from datetime import datetime, timedelta

class PermissionCache:
    def __init__(self):
        self.cache: Dict[int, Dict[str, Any]] = {}
        self.cache_time: Dict[int, float] = {}
        self.CACHE_DURATION = 60  # 1 minute in seconds

    def get_cache(self, guild_id: int) -> Optional[Dict[str, Any]]:
        if guild_id in self.cache and time.time() - self.cache_time.get(guild_id, 0) < self.CACHE_DURATION:
            return self.cache[guild_id]
        return None

    def update_cache(self, guild_id: int, data: Dict[str, Any]):
        self.cache[guild_id] = data
        self.cache_time[guild_id] = time.time()

class Pencil(commands.GroupCog, name="perm_editor"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.cache = PermissionCache()
        self.source_type: Dict[int, str] = {}

    async def _format_permission_changes(
        self,
        title: str,
        description: str,
        changes: List[Tuple[str, List[Tuple[str, bool]]]],
        color: discord.Color = discord.Color.green()
    ) -> Tuple[discord.Embed, File]:
        """Format permission changes into a text file with all changes."""
        # Count total changes for summary
        total_allowed = 0
        total_denied = 0
        total_neutral = 0
        
        # First pass to count all permission states
        for _, perm_changes in changes:
            for perm, value in perm_changes:
                if value is True:
                    total_allowed += 1
                elif value is False:
                    total_denied += 1
                else:  # None/Neutral
                    total_neutral += 1
        
        # Create header for the text file
        header = [
            "=" * 80,
            f"PERMISSION CHANGES - {title.upper()}",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 80,
            "",
            description,
            "",
            f"Total Items Modified: {len(changes)}\n"
            f"Total Permissions Allowed: {total_allowed}\n"
            f"Total Permissions Denied: {total_denied}\n"
            f"Total Permissions Set to Neutral: {total_neutral}",
            "=" * 80,
            ""
        ]
        
        # Process all changes
        text_lines = []
        for entity_name, perm_changes in changes:
            # Group permissions by their state (allowed/denied/neutral)
            allowed = [perm for perm, value in perm_changes if value is True]
            denied = [perm for perm, value in perm_changes if value is False]
            neutral = [perm for perm, value in perm_changes if value is None]
            
            # Format for text file
            text_lines.extend([
                f"[ {entity_name} ]",
                "-" * (len(entity_name) + 4)
            ])
            
            if allowed:
                text_lines.append("✅ Allowed Permissions:")
                for perm in sorted(allowed):
                    text_lines.append(f"  • {perm}")
            
            if denied:
                if allowed:
                    text_lines.append("")  # Add a blank line between sections
                text_lines.append("❌ Denied Permissions:")
                for perm in sorted(denied):
                    text_lines.append(f"  • {perm}")
                
            if neutral:
                if allowed or denied:
                    text_lines.append("")  # Add a blank line between sections
                text_lines.append("➖ Neutral/Default Permissions:")
                for perm in sorted(neutral):
                    text_lines.append(f"  • {perm}")
            
            text_lines.append("\n" + ("-" * 50) + "\n")
        
        # Create the file
        full_text = "\n".join(header + text_lines)
        buffer = io.BytesIO(full_text.encode('utf-8'))
        file = File(buffer, filename=f"permission_changes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        
        # Create a simple embed for the file
        file_embed = discord.Embed(
            title=f"{title} - Permission Changes",
            description=(
                f"{description}\n\n"
                f"**Total Changes:** {len(changes)}\n"
                f"**✅ Allowed:** {total_allowed}\n"
                f"**❌ Denied:** {total_denied}\n"
                f"**➖ Neutral:** {total_neutral}\n\n"
                "All changes have been saved to the attached file."
            ),
            color=color,
            timestamp=datetime.now()
        )
        
        return file_embed, file

    async def cache_guild_data(self, guild: discord.Guild) -> Dict[str, Any]:
        """Cache guild channels and roles for autocomplete."""
        cached = self.cache.get_cache(guild.id)
        if cached is not None:
            return cached

        data = {
            'text_channels': [],
            'voice_channels': [],
            'categories': [],
            'stages': [],
            'forums': [],
            'roles': []
        }

        # Cache channels
        for channel in guild.channels:
            if isinstance(channel, discord.TextChannel):
                data['text_channels'].append(channel)
            elif isinstance(channel, discord.VoiceChannel):
                data['voice_channels'].append(channel)
            elif isinstance(channel, discord.CategoryChannel):
                data['categories'].append(channel)
            elif isinstance(channel, discord.StageChannel):
                data['stages'].append(channel)
            elif isinstance(channel, discord.ForumChannel):
                data['forums'].append(channel)

        # Cache roles (excluding @everyone)
        data['roles'] = [role for role in guild.roles if not role.is_default()]

        self.cache.update_cache(guild.id, data)
        return data

    async def get_source_type(self, guild: discord.Guild, source_id: int) -> Optional[str]:
        """Get the type of the source (text, voice, category, stage, forum, role)."""
        data = await self.cache_guild_data(guild)
        
        for channel_type in ['text_channels', 'voice_channels', 'categories', 'stages', 'forums']:
            if any(channel.id == source_id for channel in data[channel_type]):
                return channel_type.replace('_channels', '').replace('categories', 'category')
                
        if any(role.id == source_id for role in data['roles']):
            return 'role'
            
        return None

    def is_guild_owner():
        """Check if the user is the server owner"""
        def predicate(interaction: discord.Interaction) -> bool:
            return interaction.user == interaction.guild.owner
        return app_commands.check(predicate)
    

    @app_commands.command(name="copy", description="Copy permissions from one channel|role to another (text|voice|category|stage|forum)")
    @is_guild_owner()
    @app_commands.describe(
        source_type="Type of source to copy from",
        source_id="ID of the source to copy from",
        target_id="ID of the target channel to paste to"
    )
    @app_commands.choices(source_type=[
        app_commands.Choice(name="Channel", value="channel"),
        app_commands.Choice(name="Role", value="role")
    ])
    async def copy_permissions(
        self,
        interaction: discord.Interaction,
        source_type: str,
        source_id: str,
        target_id: str,
        confirm: bool = False
    ):
        """Copy permissions from a source to a target."""
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        if not confirm:
            await interaction.response.send_message(
                "Please confirm the action by using the `/pencil copy` command with the `confirm` parameter set to `true`.",
                ephemeral=True
            )
            return

        # Check permissions based on source type
        if source_type == 'channel':
            # Get source and target channels
            source_channel = interaction.guild.get_channel(int(source_id))
            target_channel = interaction.guild.get_channel(int(target_id))
            
            # Check if channels exist
            if not source_channel or not target_channel:
                return await interaction.response.send_message(
                    "One or both channels could not be found.",
                    ephemeral=True
                )
                
            # Check if user has manage_channel permission in source channel
            if not source_channel.permissions_for(interaction.user).manage_channels:
                return await interaction.response.send_message(
                    f"You need 'Manage Channel' permission in the source channel ({source_channel.mention}).",
                    ephemeral=True
                )
                
            # Check if user has manage_channel permission in target channel
            if not target_channel.permissions_for(interaction.user).manage_channels:
                return await interaction.response.send_message(
                    f"You need 'Manage Channel' permission in the target channel ({target_channel.mention}).",
                    ephemeral=True
                )
        elif source_type == 'role':
            if not interaction.user.guild_permissions.manage_roles:
                return await interaction.response.send_message(
                    "You need 'Manage Roles' permission to use this command.",
                    ephemeral=True
                )
                
            # Get source and target roles
            source_role = interaction.guild.get_role(int(source_id))
            target_role = interaction.guild.get_role(int(target_id))
            user_highest_role = interaction.user.top_role
            
            # Check if roles exist
            if not source_role or not target_role:
                return await interaction.response.send_message(
                    "One or both roles could not be found.",
                    ephemeral=True
                )
                
            # Check if source role is above user's highest role
            if source_role >= user_highest_role:
                return await interaction.response.send_message(
                    f"You cannot modify permissions for {source_role.mention} as it is equal to or higher than your highest role.",
                    ephemeral=True
                )
                
            # Check if target role is above user's highest role
            if target_role >= user_highest_role:
                return await interaction.response.send_message(
                    f"You cannot modify permissions for {target_role.mention} as it is equal to or higher than your highest role.",
                    ephemeral=True
                )

        await interaction.response.defer(ephemeral=True)

        try:
            # Convert string IDs to integers
            try:
                source_id = int(source_id)
                target_id = int(target_id)
            except ValueError:
                return await interaction.followup.send("❌ Invalid source or target ID.", ephemeral=True)

            if source_type == 'channel':
                # Handle channel-to-channel copying
                target_channel = interaction.guild.get_channel(target_id)
                if not target_channel or not isinstance(target_channel, (discord.TextChannel, discord.VoiceChannel, 
                                                                      discord.CategoryChannel, discord.StageChannel, 
                                                                      discord.ForumChannel)):
                    return await interaction.followup.send("❌ Target channel not found or invalid type.", ephemeral=True)

                source_channel = interaction.guild.get_channel(source_id)
                if not source_channel or not isinstance(source_channel, (discord.TextChannel, discord.VoiceChannel, 
                                                                       discord.CategoryChannel, discord.StageChannel, 
                                                                       discord.ForumChannel)):
                    return await interaction.followup.send("❌ Source channel not found or invalid type.", ephemeral=True)

                # Get the permission differences
                old_overwrites = target_channel.overwrites
                await target_channel.edit(overwrites=source_channel.overwrites)
                new_overwrites = target_channel.overwrites
                
                # Find changed permissions
                changed_perms = []
                all_entities = set(old_overwrites.keys()) | set(new_overwrites.keys())
                
                for entity in all_entities:
                    old_perm = old_overwrites.get(entity, discord.PermissionOverwrite())
                    new_perm = new_overwrites.get(entity, discord.PermissionOverwrite())
                    
                    # Get permission changes
                    perm_changes = []
                    for perm, value in new_perm:
                        old_value = getattr(old_perm, perm, None)
                        if old_value != value:  # Changed to include None values
                            perm_changes.append((perm, value))  # Now includes None/neutral values
                    
                    if perm_changes:
                        if isinstance(entity, discord.Role):
                            name = f"@{entity.name}"
                        else:
                            name = f"{entity.name} ({entity.id})"
                        changed_perms.append((name, perm_changes))
                
                # Format the message
                title = f"✅ Copied Permissions"
                description = f"From: {source_channel.mention}\nTo: {target_channel.mention}"
                
                if changed_perms:
                    result = await self._format_permission_changes(
                        title=title,
                        description=description,
                        changes=changed_perms,
                        color=discord.Color.green()
                    )
                else:
                    embed = discord.Embed(
                        title=title,
                        description=f"{description}\n\nNo permission changes were needed (permissions were already set as requested)",
                        color=discord.Color.green()
                    )
                    result = embed
                
                if isinstance(result, tuple):
                    await interaction.followup.send(embed=result[0], file=result[1], ephemeral=True)
                elif isinstance(result, list):
                    for embed in result:
                        await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(embed=result, ephemeral=True)

            else:  # role-to-role copying
                source_role = interaction.guild.get_role(source_id)
                target_role = interaction.guild.get_role(int(target_id))
                
                if not source_role:
                    return await interaction.followup.send("❌ Source role not found.", ephemeral=True)
                if not target_role:
                    return await interaction.followup.send("❌ Target role not found.", ephemeral=True)
                if source_role == target_role:
                    return await interaction.followup.send("❌ Cannot copy permissions to the same role.", ephemeral=True)

                # Get old and new permissions
                old_permissions = target_role.permissions
                new_permissions = source_role.permissions
                
                # Find changed permissions
                perm_changes = []
                for perm, value in new_permissions:
                    old_value = getattr(old_permissions, perm, None)
                    if old_value != value:  # This will now include None/neutral values
                        perm_changes.append((perm, value))  # Includes None/neutral values
                
                # Apply new permissions
                await target_role.edit(permissions=new_permissions)
                
                # Copy role position if possible
                position_changed = False
                if (target_role.position < interaction.guild.me.top_role.position and 
                    target_role.position != source_role.position):
                    try:
                        await target_role.edit(position=source_role.position)
                        position_changed = True
                    except:
                        pass  # Ignore if we can't change position
                
                # Format the success message
                title = f"✅ Copied Role Permissions"
                description = f"From: {source_role.mention}\nTo: {target_role.mention}"
                
                if position_changed:
                    description += f"\n📌 Updated role position to {source_role.position}"
                
                if perm_changes:
                    result = await self._format_permission_changes(
                        title=title,
                        description=description,
                        changes=[("Permission Changes", perm_changes)],
                        color=discord.Color.green()
                    )
                else:
                    embed = discord.Embed(
                        title=title,
                        description=f"{description}\n\nNo permission changes were needed (permissions were already set as requested)",
                        color=discord.Color.green()
                    )
                    result = embed
                
                if isinstance(result, tuple):
                    await interaction.followup.send(embed=result[0], file=result[1], ephemeral=True)
                elif isinstance(result, list):
                    for embed in result:
                        await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(embed=result, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

    @copy_permissions.autocomplete('source_id')
    async def source_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for the source_id parameter with permission checks."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return []

        try:
            # Get the source_type from the current interaction with a safe default
            source_type = getattr(interaction.namespace, 'source_type', None)
            data = await self.cache_guild_data(interaction.guild)
            
            all_items = []
            member = interaction.user
            
            # If source_type is not set or is 'channel', include channels
            if source_type != 'role':
                for channel in data['text_channels'] + data['voice_channels'] + data['categories'] + data['stages'] + data['forums']:
                    # Check if user can view and manage the channel
                    if channel.permissions_for(member).view_channel and channel.permissions_for(member).manage_channels:
                        if isinstance(channel, discord.TextChannel):
                            prefix = "#"
                        elif isinstance(channel, discord.VoiceChannel):
                            prefix = "🔊"
                        elif isinstance(channel, discord.CategoryChannel):
                            prefix = "📁"
                        elif isinstance(channel, discord.StageChannel):
                            prefix = "🎤"
                        else:  # ForumChannel
                            prefix = "📝"
                        all_items.append((f"{prefix} {channel.name}", str(channel.id)))
            
            # If source_type is 'role' or not set, and user has manage_roles permission, include roles
            if source_type != 'channel' and member.guild_permissions.manage_roles:
                # Only show roles lower than the user's highest role
                highest_role = member.top_role
                for role in data['roles']:
                    if role < highest_role or member.guild_permissions.administrator:
                        all_items.append((f"👔 {role.name}", str(role.id)))
            
            # Filter based on current input
            filtered = [
                app_commands.Choice(name=name, value=item_id)
                for name, item_id in all_items
                if current.lower() in name.lower()
            ][:25]  # Limit to 25 choices
            
            return filtered
            
        except Exception as e:
            print(f"Error in source_id_autocomplete: {e}")
            return []

    @copy_permissions.autocomplete('target_id')
    async def target_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for the target_id parameter with permission checks."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return []

        try:
            # Safely get source_type and source_id from the interaction namespace
            source_type = getattr(interaction.namespace, 'source_type', None)
            source_id = getattr(interaction.namespace, 'source_id', None)
            data = await self.cache_guild_data(interaction.guild)
            member = interaction.user
            
            all_items = []
            
            # If source is a role, show only roles as potential targets
            if source_type == 'role' and source_id:
                if member.guild_permissions.manage_roles:
                    # Only show roles lower than the user's highest role
                    highest_role = member.top_role
                    for role in data['roles']:
                        if (str(role.id) != str(source_id) and 
                            (role < highest_role or member.guild_permissions.administrator)):
                            all_items.append((f"👔 {role.name}", str(role.id)))
            else:
                # For channels, check view and manage permissions
                for channel in data['text_channels'] + data['voice_channels'] + data['categories'] + data['stages'] + data['forums']:
                    if channel.permissions_for(member).view_channel and channel.permissions_for(member).manage_channels:
                        if isinstance(channel, discord.TextChannel):
                            prefix = "#"
                        elif isinstance(channel, discord.VoiceChannel):
                            prefix = "🔊"
                        elif isinstance(channel, discord.CategoryChannel):
                            prefix = "📁"
                        elif isinstance(channel, discord.StageChannel):
                            prefix = "🎤"
                        else:  # ForumChannel
                            prefix = "📝"
                        all_items.append((f"{prefix} {channel.name}", str(channel.id)))
            
            # Filter based on current input
            filtered = [
                app_commands.Choice(name=name, value=item_id)
                for name, item_id in all_items
                if current.lower() in name.lower()
            ][:25]  # Limit to 25 choices
            
            return filtered
            
        except Exception as e:
            print(f"Error in target_id_autocomplete: {e}")
            return []

    @app_commands.command(name="edit", description="Toggle all permissions for a role or channel. (text|voice|category|stage|forum)")
    @is_guild_owner()
    @app_commands.describe(
        item_type="Type of item to edit",
        item_id="ID of the item to edit",
        state="Permission state to set (on/off/neutral, off/on for roles)"
    )
    @app_commands.choices(
        item_type=[
            app_commands.Choice(name="Channel", value="channel"),
            app_commands.Choice(name="Role", value="role")
        ],
        state=[
            app_commands.Choice(name="On (Allow)", value="on"),
            app_commands.Choice(name="Off (Deny)", value="off"),
            app_commands.Choice(name="Neutral (Reset)", value="neutral")
        ]
    )
    async def edit_permissions(
        self,
        interaction: discord.Interaction,
        item_type: str,
        item_id: str,
        state: str,
        confirm: bool = False,
    ):
        """Toggle all permissions for a specific role or channel."""
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)

        if not confirm:
            await interaction.response.send_message(
                "Please confirm the action by using the `/pencil edit` command with the `confirm` parameter set to `true`.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Convert item_id to integer
            try:
                item_id = int(item_id)
            except ValueError:
                return await interaction.followup.send("❌ Invalid item ID.", ephemeral=True)

            member = interaction.user
            if not isinstance(member, discord.Member):
                return await interaction.followup.send("❌ Could not get member information.", ephemeral=True)

            if item_type == 'channel':
                # Handle channel permissions
                channel = interaction.guild.get_channel(item_id)
                if not channel:
                    return await interaction.followup.send("❌ Channel not found.", ephemeral=True)

                # Check if user can manage channels
                if not channel.permissions_for(member).manage_channels:
                    return await interaction.followup.send("❌ You don't have permission to manage this channel.", ephemeral=True)

                # Get all permissions
                perms = channel.overwrites
                changes = []
                
                # Get all permission targets (including @everyone)
                targets = list(perms.items())
                # Add @everyone if not already in the list
                if interaction.guild.default_role not in [t[0] for t in targets]:
                    targets.append((interaction.guild.default_role, None))
                
                for target, overwrite in targets:
                    # Create new permission overwrite
                    new_perms = discord.PermissionOverwrite()
                    
                    # If there are existing permissions, use them as a base
                    existing_perms = {}
                    if overwrite is not None:
                        existing_perms = {perm: value for perm, value in overwrite}
                    
                    # Get all possible permissions for the target
                    all_perms = list(discord.Permissions())
                    
                    # Set all permissions based on state
                    for perm, _ in all_perms:
                        if state == 'on':
                            setattr(new_perms, perm, True)
                        elif state == 'off':
                            setattr(new_perms, perm, False)
                        # For neutral, we'll delete the overwrite
                    
                    changes.append((target, new_perms))
                
                # Apply changes
                for target, new_perms in changes:
                    if state == 'neutral':
                        await channel.set_permissions(target, overwrite=None)
                    else:
                        await channel.set_permissions(target, overwrite=new_perms)
                
                await interaction.followup.send(f"✅ Updated all permissions for {channel.mention} to '{state}'", ephemeral=True)
                
            elif item_type == 'role':
                # Handle role permissions
                if not member.guild_permissions.manage_roles:
                    return await interaction.followup.send("❌ You need 'Manage Roles' permission to edit role permissions.", ephemeral=True)
                
                role = interaction.guild.get_role(item_id)
                if not role:
                    return await interaction.followup.send("❌ Role not found.", ephemeral=True)
                
                # Check if user can modify this role
                if role >= member.top_role and not member.guild_permissions.administrator:
                    return await interaction.followup.send("❌ You can only edit roles below your highest role.", ephemeral=True)
                
                # Create new permissions
                new_perms = discord.Permissions()
                
                # Set all permissions based on state
                for perm, _ in role.permissions:
                    setattr(new_perms, perm, state == 'on')
                
                # Update role permissions
                await role.edit(permissions=new_perms)
                
                await interaction.followup.send(f"✅ Updated all permissions for role @{role.name} to '{state}'", ephemeral=True)
            
            else:
                await interaction.followup.send("❌ Invalid item type.", ephemeral=True)
                
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
            
    @edit_permissions.autocomplete('item_id')
    async def edit_item_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for the item_id parameter in edit command with permission checks."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return []

        try:
            # Get the item_type from the current interaction with a safe default
            item_type = getattr(interaction.namespace, 'item_type', None)
            data = await self.cache_guild_data(interaction.guild)
            
            all_items = []
            member = interaction.user
            
            # If item_type is channel or not set, include channels
            if item_type != 'role':
                for channel in data['text_channels'] + data['voice_channels'] + data['categories'] + data['stages'] + data['forums']:
                    # Check if user can manage the channel
                    if channel.permissions_for(member).manage_channels:
                        if isinstance(channel, discord.TextChannel):
                            prefix = "#"
                        elif isinstance(channel, discord.VoiceChannel):
                            prefix = "🔊"
                        elif isinstance(channel, discord.CategoryChannel):
                            prefix = "📁"
                        elif isinstance(channel, discord.StageChannel):
                            prefix = "🎤"
                        else:  # ForumChannel
                            prefix = "📝"
                        all_items.append((f"{prefix} {channel.name}", str(channel.id)))
            
            # If item_type is role or not set, and user has manage_roles permission, include roles
            if item_type != 'channel' and member.guild_permissions.manage_roles:
                # Only show roles lower than the user's highest role
                highest_role = member.top_role
                for role in data['roles']:
                    if role < highest_role or member.guild_permissions.administrator:
                        all_items.append((f"👔 {role.name}", str(role.id)))
            
            # Filter based on current input
            filtered = [
                app_commands.Choice(name=name, value=item_id)
                for name, item_id in all_items
                if current.lower() in name.lower()
            ][:25]  # Limit to 25 choices
            
            return filtered
            
        except Exception as e:
            print(f"Error in edit_item_id_autocomplete: {e}")
            return []

    @app_commands.command(name="view", description="View permissions for a role or channel. (text|voice|category|stage|forum)")
    @is_guild_owner()
    @app_commands.describe(
        item_type="Type of item to view permissions for",
        item_id="ID of the item to view"
    )
    @app_commands.choices(item_type=[
        app_commands.Choice(name="Channel", value="channel"),
        app_commands.Choice(name="Role", value="role")
    ])
    async def view_permissions(
        self,
        interaction: discord.Interaction,
        item_type: str,
        item_id: str
    ):
        """View permissions for a specific role or channel."""
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            # Convert item_id to integer
            try:
                item_id = int(item_id)
            except ValueError:
                return await interaction.followup.send("❌ Invalid item ID.", ephemeral=True)

            if item_type == 'channel':
                # Handle channel permissions
                channel = interaction.guild.get_channel(item_id)
                if not channel:
                    return await interaction.followup.send("❌ Channel not found.", ephemeral=True)

                # Check if user has view_channel permission
                if not channel.permissions_for(interaction.user).view_channel:
                    return await interaction.followup.send("❌ You don't have permission to view this channel.", ephemeral=True)

                # Get all permission overwrites
                overwrites = []
                for target, overwrite in channel.overwrites.items():
                    if isinstance(target, discord.Role):
                        name = f"@{target.name}"
                    else:
                        name = f"{target.name} ({target.id})"
                    
                    # Format allowed, denied, and neutral permissions
                    allowed = [perm for perm, value in overwrite if value is True]
                    denied = [perm for perm, value in overwrite if value is False]
                    neutral = [perm for perm, value in overwrite if value is None]
                    
                    if allowed or denied or neutral:
                        overwrites.append((name, allowed, denied, neutral))

                # Create a formatted message
                title = f"🔍 Channel Permissions: #{channel.name}"
                description = [
                    f"Channel: #{channel.name}",
                    f"Type: {channel.type.name}",
                    f"ID: {channel.id}",
                    ""
                ]
                
                # Add channel-wide permissions
                description.append("CHANNEL PERMISSIONS")
                description.append("=" * 40)
                
                default_perms = channel.permissions_for(interaction.guild.default_role)
                allowed = [p.replace('_', ' ').title() for p, v in default_perms if v]
                denied = [p.replace('_', ' ').title() for p, v in default_perms if not v]
                
                if allowed:
                    description.append("✅ ALLOWED")
                    description.extend(f"• {p}" for p in sorted(allowed))
                
                if denied:
                    description.append("\n❌ DENIED")
                    description.extend(f"• {p}" for p in sorted(denied))
                
                # Add permission overwrites
                if overwrites:
                    description.append("\nPERMISSION OVERRIDES")
                    description.append("=" * 40)
                    
                    for name, allowed, denied, neutral in overwrites:
                        description.append(f"\n{name}")
                        
                        if allowed:
                            description.append("✅ ALLOWED")
                            description.extend(f"• {p.replace('_', ' ').title()}" for p in sorted(allowed))
                        
                        if denied:
                            description.append("\n❌ DENIED")
                            description.extend(f"• {p.replace('_', ' ').title()}" for p in sorted(denied))
                            
                        if neutral:
                            description.append("\n➖ NEUTRAL")
                            description.extend(f"• {p.replace('_', ' ').title()}" for p in sorted(neutral))
                
                description = "\n".join(description)

            else:  # role
                # Handle role permissions
                role = interaction.guild.get_role(item_id)
                if not role:
                    return await interaction.followup.send("❌ Role not found.", ephemeral=True)

                # Check if user can manage roles
                if not interaction.user.guild_permissions.manage_roles:
                    return await interaction.followup.send("❌ You need 'Manage Roles' permission to view role permissions.", ephemeral=True)

                # Get role permissions
                permissions = role.permissions
                allowed_perms = [p.replace('_', ' ').title() for p, v in permissions if v]
                denied_perms = [p.replace('_', ' ').title() for p, v in permissions if not v]

                # Create a formatted message
                title = f"👔 Role Permissions: {role.name}"
                description = [
                    f"Role: @{role.name}",
                    f"Color: {str(role.color)}",
                    f"Position: {role.position}",
                    f"ID: {role.id}",
                    ""
                ]
                
                if allowed_perms:
                    description.append("✅ ALLOWED PERMISSIONS")
                    description.append("=" * 40)
                    description.extend(f"• {p}" for p in sorted(allowed_perms))
                
                if denied_perms:
                    description.append("\n❌ DENIED PERMISSIONS")
                    description.append("=" * 40)
                    description.extend(f"• {p}" for p in sorted(denied_perms))
                
                description = "\n".join(description)

            # Create a text file with the permissions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{'channel' if item_type == 'channel' else 'role'}_permissions_{timestamp}.txt"
            
            # Create file content with a header
            header = [
                "=" * 80,
                title.upper(),
                f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"Server: {interaction.guild.name}",
                "=" * 80,
                ""
            ]
            
            full_content = "\n".join(header) + "\n" + description
            buffer = io.BytesIO(full_content.encode('utf-8'))
            file = File(buffer, filename=filename)
            
            # Create an embed for the response
            embed = discord.Embed(
                title=title,
                description=f"Permissions have been saved to the attached file.\n\n"
                          f"**Item Type:** {item_type.title()}\n"
                          f"**Item ID:** `{item_id}`",
                color=discord.Color.blue()
            )
            
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

    @view_permissions.autocomplete('item_id')
    async def view_item_id_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for the item_id parameter in view command with permission checks."""
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return []

        try:
            # Get the item_type from the current interaction
            item_type = getattr(interaction.namespace, 'item_type', None)
            data = await self.cache_guild_data(interaction.guild)
            
            all_items = []
            member = interaction.user
            
            if item_type == 'role':
                # For roles, check if user has manage_roles permission
                if member.guild_permissions.manage_roles:
                    # Only show roles lower than the user's highest role
                    highest_role = member.top_role
                    for role in data['roles']:
                        if role < highest_role or member.guild_permissions.administrator:
                            all_items.append((f"👔 {role.name}", str(role.id)))
            else:  # Default to showing channels if no type or type is channel
                # For channels, check view and manage permissions
                for channel in data['text_channels'] + data['voice_channels'] + data['categories'] + data['stages'] + data['forums']:
                    if isinstance(channel, discord.TextChannel):
                        prefix = "#"
                    elif isinstance(channel, discord.VoiceChannel):
                        prefix = "🔊"
                    elif isinstance(channel, discord.CategoryChannel):
                        prefix = "📁"
                    elif isinstance(channel, discord.StageChannel):
                        prefix = "🎤"
                    else:  # ForumChannel
                        prefix = "📝"
                    
                    # Check if user can view and manage the channel
                    if channel.permissions_for(member).view_channel and channel.permissions_for(member).manage_channels:
                        all_items.append((f"{prefix} {channel.name}", str(channel.id)))
            
            # Filter based on current input
            filtered = [
                app_commands.Choice(name=name, value=item_id)
                for name, item_id in all_items
                if current.lower() in name.lower()
            ][:25]  # Limit to 25 choices
            
            return filtered
            
        except Exception as e:
            print(f"Error in view_item_id_autocomplete: {e}")
            return []

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pencil(bot))