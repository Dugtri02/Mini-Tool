# Authorize a role (or user) to be able to modify its own or another role's name, color, or icon
# includes integration with the fabric.py system

import discord; from discord import app_commands, utils; from discord.ext import commands, tasks
import sqlite3, asyncio, re
import time; from datetime import datetime
from typing import Optional, List, Dict, Any, Set, Tuple, Deque

class Wardrobe_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        # Cache structure: {guild_id: {'data': role_data, 'expires': timestamp}}
        self._cache = {}
        self._cache_timeout = 3600  # 1 hour in seconds
        self._create_tables()
    
    def _get_cache_key(self, guild_id: int) -> str:
        """Generate a consistent cache key for a guild."""
        return f"wardrobe_roles_{guild_id}"
    
    async def get_wardrobe_roles(self, guild_id: int) -> dict:
        """
        Get wardrobe roles for a guild, using cache if available.
        
        Args:
            guild_id: The ID of the guild to get roles for
            
        Returns:
            dict: A dictionary mapping role IDs to their permissions
        """
        cache_key = self._get_cache_key(guild_id)
        current_time = time.time()
        
        # Return cached data if it exists and hasn't expired
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if current_time < cache_entry['expires']:
                return cache_entry['data']
        
        # Fetch from database if not in cache or expired
        with self.db:
            cursor = self.db.cursor()
            cursor.execute('''
                SELECT role_id, can_name, can_colour, can_icon
                FROM wardrobe_roles
                WHERE guild_id = ?
            ''', (guild_id,))
            
            roles = {}
            for row in cursor.fetchall():
                role_id, can_name, can_colour, can_icon = row
                roles[role_id] = {
                    'can_name': bool(can_name),
                    'can_colour': bool(can_colour),
                    'can_icon': bool(can_icon)
                }
        
        # Update cache
        self._cache[cache_key] = {
            'data': roles,
            'expires': current_time + self._cache_timeout
        }
        
        return roles
    
    def invalidate_wardrobe_cache(self, guild_id: int):
        """
        Invalidate the cache for a specific guild.
        
        Args:
            guild_id: The ID of the guild to invalidate cache for
        """
        cache_key = self._get_cache_key(guild_id)
        if cache_key in self._cache:
            del self._cache[cache_key]
    
    def _create_tables(self):
        """Create the necessary database tables for wardrobe functionality."""
        with self.db:  # This ensures the connection is committed or rolled back
            cursor = self.db.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wardrobe_roles (
                    guild_id INTEGER NOT NULL,
                    role_id INTEGER NOT NULL,
                    can_name BOOLEAN DEFAULT 0,
                    can_colour BOOLEAN DEFAULT 0,
                    can_icon BOOLEAN DEFAULT 0,
                    created_by_role_id INTEGER,
                    created_by_user_id INTEGER,
                    PRIMARY KEY (guild_id, role_id)
                )
            ''')
            
            # Create indexes for faster lookups
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_wardrobe_guild 
                ON wardrobe_roles (guild_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_wardrobe_role 
                ON wardrobe_roles (role_id)
            ''')
    
    async def cog_check(self, ctx):
        """Global check for all commands in this cog."""
        # Add any global permission checks here
        return True
        
    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Clean up member's wardrobe role assignments when they leave the server."""
        try:
            with self.db:
                cursor = self.db.cursor()
                # Remove any entries where this member was a creator of a wardrobe role
                cursor.execute('''
                    UPDATE wardrobe_roles 
                    SET created_by_user_id = NULL 
                    WHERE guild_id = ? AND created_by_user_id = ?
                ''', (member.guild.id, member.id))
                
                # Invalidate cache if any changes were made
                if cursor.rowcount > 0:
                    self.invalidate_wardrobe_cache(member.guild.id)
                    
        except Exception as e:
            print(f"Error in on_member_remove for {member.id}: {e}")
    
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """Remove deleted roles from the wardrobe system."""
        try:
            # Remove the role from the database if it exists
            with self.db:
                cursor = self.db.cursor()
                cursor.execute('''
                    DELETE FROM wardrobe_roles 
                    WHERE role_id = ?
                ''', (role.id,))
                
                # If any rows were affected, invalidate the cache
                if cursor.rowcount > 0:
                    self.invalidate_wardrobe_cache(role.guild.id)
                    
        except Exception as e:
            print(f"Error in on_guild_role_delete for role {role.id}: {e}")
    
    @app_commands.command(name="delete", description="[Admin] Remove a role from the wardrobe system")
    @app_commands.describe(
        role="The role to remove from the wardrobe system"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_wardrobe_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role
    ):
        """[Admin] Remove a role from the wardrobe system."""
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        
        try:
            # Verify the role exists in the wardrobe system
            cursor = self.db.cursor()
            cursor.execute('''
                SELECT 1 FROM wardrobe_roles 
                WHERE guild_id = ? AND role_id = ?
            ''', (interaction.guild.id, role.id))
            
            if not cursor.fetchone():
                return await interaction.response.send_message(
                    f"❌ {role.mention} is not configured in the wardrobe system.",
                    ephemeral=True
                )
            
            # Remove the role from the wardrobe system
            cursor.execute('''
                DELETE FROM wardrobe_roles 
                WHERE guild_id = ? AND role_id = ?
            ''', (interaction.guild.id, role.id))
            
            self.db.commit()
            
            # Invalidate the cache for this guild
            self.invalidate_wardrobe_cache(interaction.guild.id)
            
            await interaction.response.send_message(
                f"✅ Successfully removed {role.mention} from the wardrobe system.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred while removing the role: {e}",
                ephemeral=True
            )
    
    @app_commands.command(name="list", description="View wardrobe role configurations")
    @app_commands.describe(
        role="(Optional) The role to view configuration for",
        page="Page number to view (for pagination)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def list_wardrobe_roles(
        self,
        interaction: discord.Interaction,
        role: Optional[discord.Role] = None,
        page: int = 1
    ):
        """View wardrobe role configurations. Shows all configured roles if no specific role is provided."""
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        
        try:
            cursor = self.db.cursor()
            
            if role:
                # Show configuration for a specific role
                cursor.execute('''
                    SELECT can_name, can_colour, can_icon, created_by_role_id, created_by_user_id
                    FROM wardrobe_roles
                    WHERE guild_id = ? AND role_id = ?
                ''', (interaction.guild.id, role.id))
                
                config = cursor.fetchone()
                if not config:
                    return await interaction.response.send_message(
                        f"❌ {role.mention} is not configured in the wardrobe system.",
                        ephemeral=True
                    )
                
                can_name, can_colour, can_icon, created_by_role_id, created_by_user_id = config
                
                embed = discord.Embed(
                    title=f"Wardrobe Configuration for {role.name}",
                    color=role.color if role.color.value != 0 else discord.Color.blue()
                )
                
                # Add basic role info
                info = [f"• **Role:** {role.mention}"]
                
                # Add permissions
                perms = []
                if can_name: perms.append("Change Name")
                if can_colour: perms.append("Change Color")
                if can_icon: perms.append("Change Icon")
                
                if perms:
                    info.append("• **Permissions:**\n  " + "\n  ".join(f"• {p}" for p in perms))
                
                # Add creator information
                creator_info = []
                if created_by_role_id:
                    creator_role = interaction.guild.get_role(created_by_role_id)
                    if creator_role:
                        creator_info.append(f"• Role: {creator_role.mention}")
                
                if created_by_user_id:
                    try:
                        creator_user = await interaction.guild.fetch_member(created_by_user_id)
                        creator_info.append(f"• User: {creator_user.mention}")
                    except:
                        creator_info.append(f"• User: <@{created_by_user_id}>")
                
                if creator_info:
                    info.append("• **Editors:**\n  " + "\n  ".join(creator_info))
                
                embed.description = "\n".join(info)
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                # List all configured roles with pagination
                cursor.execute('''
                    SELECT role_id, can_name, can_colour, can_icon, created_by_role_id, created_by_user_id
                    FROM wardrobe_roles
                    WHERE guild_id = ?
                    ORDER BY role_id
                ''', (interaction.guild.id,))
                
                all_roles = cursor.fetchall()
                if not all_roles:
                    return await interaction.response.send_message(
                        "❌ No roles are configured in the wardrobe system.",
                        ephemeral=True
                    )
                
                # Split roles into pages (8 roles per page)
                ROLES_PER_PAGE = 8
                pages = [all_roles[i:i + ROLES_PER_PAGE] for i in range(0, len(all_roles), ROLES_PER_PAGE)]
                
                # Adjust page number to be within valid range
                page = max(1, min(page, len(pages)))
                current_page = page - 1
                page_roles = pages[current_page]
                
                # Create embed with pagination info
                embed = discord.Embed(
                    title="Wardrobe Role Configurations",
                    description=f"Page {page} of {len(pages)}",
                    color=discord.Color.blue()
                )
                
                # Add roles for current page
                for role_id, can_name, can_colour, can_icon, created_by_role_id, created_by_user_id in page_roles:
                    role = interaction.guild.get_role(role_id)
                    if not role:
                        continue  # Skip deleted roles
                    
                    # Build role info
                    role_info = [f"• **Role:** {role.mention}"]
                    
                    # Add permissions
                    perms = []
                    if can_name: perms.append("Change Name")
                    if can_colour: perms.append("Change Color")
                    if can_icon: perms.append("Change Icon")
                    
                    if perms:
                        role_info.append("• **Permissions:**\n  " + "\n  ".join(f"• {p}" for p in perms))
                    
                    # Add creator information
                    creator_info = []
                    if created_by_role_id:
                        creator_role = interaction.guild.get_role(created_by_role_id)
                        if creator_role:
                            creator_info.append(f"• Role: {creator_role.mention}")
                    
                    if created_by_user_id:
                        try:
                            creator_user = await interaction.guild.fetch_member(created_by_user_id)
                            creator_info.append(f"• User: {creator_user.mention}")
                        except:
                            creator_info.append(f"• User: <@{created_by_user_id}>")
                    
                    if creator_info:
                        role_info.append("• **Editors:**\n  " + "\n  ".join(creator_info))
                    
                    # Add field with bullet points
                    embed.add_field(
                        name=role.name,
                        value="\n".join(role_info),
                        inline=False
                    )
                
                # Add pagination footer if there are multiple pages
                if len(pages) > 1:
                    embed.set_footer(text=f"Use `/wardrobe list page:<number>` to view other pages")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred while fetching role configurations: {e}",
                ephemeral=True
            )
    
    @app_commands.command(name="setup", description="Set up a role to be able to modify its own name, color, or icon")
    @app_commands.describe(
        role="The role to set up",
        can_name="Allow members with this role to change the role's name",
        can_colour="Allow members with this role to change the role's color",
        can_icon="Allow members with this role to change the role's icon",
        editor_role="The role that will be able to edit this role",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(manage_roles=True)
    async def setup_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        can_name: bool = False,
        can_colour: bool = False,
        can_icon: bool = False,
        editor_role: discord.Role = None,
        editor_user: discord.User = None,
    ):
        """
        Set up a role to allow its members to modify its name, color, or icon.
        
        Parameters
        -----------
        interaction: discord.Interaction
            The interaction that triggered this command
        role: discord.Role
            The role to set up
        can_name: bool
            Whether members with this role can change the role's name
        can_colour: bool
            Whether members with this role can change the role's color
        can_icon: bool
            Whether members with this role can change the role's icon
        """
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        
        # Ensure the bot's role is higher than the target role
        if interaction.guild.me.top_role <= role:
            return await interaction.response.send_message(
                f"My highest role must be above the {role.name} role in the role hierarchy.",
                ephemeral=True
            )
        
        # Ensure the user's role is higher than the target role
        if interaction.user.top_role <= role and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                f"Your highest role must be above the {role.name} role in the role hierarchy.",
                ephemeral=True
            )
        
        try:
            with self.db:
                cursor = self.db.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO wardrobe_roles (
                        guild_id, role_id, can_name, can_colour, can_icon, created_by_role_id, created_by_user_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    interaction.guild.id,
                    role.id,
                    int(can_name),
                    int(can_colour),
                    int(can_icon),
                    editor_role.id if editor_role else None,
                    editor_user.id if editor_user else None,
                ))
            
            # Invalidate the cache for this guild
            self.invalidate_wardrobe_cache(interaction.guild.id)
            
            # Build permission string
            permissions = []
            if can_name:
                permissions.append("change name")
            if can_colour:
                permissions.append("change color")
            if can_icon:
                permissions.append("change icon")
            if editor_role:
                permissions.append(f"edited by {editor_role.name}")
            if editor_user:
                permissions.append(f"edited by {editor_user.name}")
            
            if not permissions:
                permission_text = "no permissions (role will be removed from the wardrobe system)"
            else:
                permission_text = ", ".join(permissions[:-1])
                if len(permissions) > 1:
                    permission_text += f" and {permissions[-1]}"
                else:
                    permission_text = permissions[0]
            
            await interaction.response.send_message(
                f"✅ Successfully set up the {role.mention} role with permission to {permission_text}.",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred while setting up the role: {str(e)}",
                ephemeral=True
            )
    
    async def _get_user_managed_roles(self, interaction: discord.Interaction) -> List[discord.Role]:
        """
        Get a list of roles the user can modify based on their permissions.
        
        A user can modify a role if:
        1. They have the role, OR
        2. They are an admin, OR
        3. They have manage_roles permission, OR
        4. One of their roles matches created_by_role_id, OR
        5. Their user ID matches created_by_user_id
        """
        if not interaction.guild:
            return []
        
        # Get all configured roles for the guild with creator information
        with self.db:
            cursor = self.db.cursor()
            cursor.execute('''
                SELECT role_id, created_by_role_id, created_by_user_id
                FROM wardrobe_roles
                WHERE guild_id = ?
            ''', (interaction.guild.id,))
            roles_config = cursor.fetchall()
        
        if not roles_config:
            return []
        
        # Get user's role IDs for comparison
        user_role_ids = {role.id for role in interaction.user.roles}
        user_id = interaction.user.id
        
        # Get roles the user has permission to modify
        managed_roles = []
        for role_id, created_by_role_id, created_by_user_id in roles_config:
            role = interaction.guild.get_role(role_id)
            if not role:
                continue
                
            # Check admin/manage_roles permissions first (admins can see everything)
            has_admin_perms = (interaction.user.guild_permissions.administrator or 
                            interaction.user.guild_permissions.manage_roles)
            if has_admin_perms:
                managed_roles.append(role)
                continue
                
            # Check if user has this role
            user_has_role = role in interaction.user.roles
            
            # Check if role has creator restrictions
            has_creator = created_by_role_id is not None or created_by_user_id is not None
            
            # If role has no creator, show to users who have the role
            if not has_creator and user_has_role:
                managed_roles.append(role)
                continue
                
            # If role has a creator, only show to creator user/role (not to users who just have the role)
            if has_creator:
                is_creator = (
                    (created_by_user_id is not None and created_by_user_id == user_id) or
                    (created_by_role_id is not None and created_by_role_id in user_role_ids)
                )
                if is_creator:
                    managed_roles.append(role)
        
        return managed_roles
    
    async def _role_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for role selection in the modify command."""
        if not interaction.guild:
            return []
            
        # Get all roles the user can manage
        managed_roles = await self._get_user_managed_roles(interaction)
        
        # Filter based on current input
        choices = []
        for role in managed_roles:
            if current.lower() in role.name.lower():
                choices.append(app_commands.Choice(
                    name=role.name,
                    value=str(role.id)
                ))
        
        return choices[:25]  # Discord limits to 25 choices

    @app_commands.command(name="modify", description="Modify a role you have permission to change the name|color|icon of (Config: Wardrobe)")
    @app_commands.describe(
        role="The role to modify (start typing to search)",
        new_name="New name for the role (leave empty to keep current)",
        new_color="New color for the role (e.g., #FF0000, FF0000, 'random', or 'remove' to clear)",
        remove_color="Set to True to remove the current color",
        icon_file="PNG file to use as the role icon (for boost level 2+ servers)",
        remove_icon="Set to True to remove the current icon"
    )
    @app_commands.autocomplete(role=_role_autocomplete)
    async def modify_role(
        self,
        interaction: discord.Interaction,
        role: str,
        new_name: Optional[str] = None,
        new_color: Optional[str] = None,
        remove_color: bool = False,
        icon_file: Optional[discord.Attachment] = None,
        remove_icon: bool = False
    ):
        # Convert role_id to int and get the role
        try:
            role_id = int(role)
            role = interaction.guild.get_role(role_id)
            if not role:
                return await interaction.response.send_message(
                    "Role not found. Please select a valid role from the autocomplete.",
                    ephemeral=True
                )
        except (ValueError, AttributeError):
            return await interaction.response.send_message(
                "Invalid role selected. Please use the autocomplete to select a role.",
                ephemeral=True
            )
        """
        Modify a role you have permission to change.
        
        Parameters
        -----------
        interaction: discord.Interaction
            The interaction that triggered this command
        role: discord.Role
            The role to modify
        new_name: Optional[str]
            New name for the role
        new_color: Optional[str]
            New color for the role (hex code or 'random')
        new_icon: Optional[str]
            New icon for the role (emoji or URL)
        """
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        
        # Get role configuration
        roles_config = await self.get_wardrobe_roles(interaction.guild.id)
        role_config = roles_config.get(role.id)
        
        if not role_config:
            return await interaction.response.send_message(
                "This role is not configured in the wardrobe system.",
                ephemeral=True
            )
        
        # Check if user has permission to modify this role
        if not (
            role in interaction.user.roles or
            interaction.user.guild_permissions.administrator or
            interaction.user.guild_permissions.manage_roles
        ):
            return await interaction.response.send_message(
                "You don't have permission to modify this role.",
                ephemeral=True
            )
        
        # Validate requested changes against permissions
        changes = {}
        
        if new_name is not None and role_config['can_name']:
            changes['name'] = new_name
        elif new_name is not None:
            return await interaction.response.send_message(
                "You don't have permission to change this role's name.",
                ephemeral=True
            )
        
        # Handle color changes
        if new_color is not None or remove_color:
            if role_config['can_colour']:
                if remove_color or (isinstance(new_color, str) and new_color.lower() == 'remove'):
                    # Remove the color by setting it to None
                    changes['color'] = discord.Color(0x000000)
                elif new_color is not None:
                    if new_color.lower() == 'random':
                        color = discord.Color.random()
                    else:
                        try:
                            # Add '#' prefix if missing and it's a valid hex color
                            if not new_color.startswith('#') and all(c.lower() in '0123456789abcdef' for c in new_color) and len(new_color) in (3, 6):
                                new_color = '#' + new_color
                            color = discord.Color.from_str(new_color)
                        except ValueError:
                            return await interaction.response.send_message(
                                "Invalid color format. Please use a hex code (e.g., FF0000 or #FF0000), 'random', or 'remove'.",
                                ephemeral=True
                            )
                    changes['color'] = color
            else:
                return await interaction.response.send_message(
                    "You don't have permission to change this role's color.",
                    ephemeral=True
                )
        
        # Handle icon changes if requested and user has permission
        if (icon_file is not None or remove_icon) and role_config['can_icon']:
            if remove_icon:
                changes['display_icon'] = None
            elif icon_file:
                if not icon_file.filename.lower().endswith('.png'):
                    return await interaction.response.send_message(
                        "Only PNG files are supported for role icons.",
                        ephemeral=True
                    )
                
                try:
                    # Check file size (max 256 KB)
                    if icon_file.size > 256 * 1024:  # 256 KB in bytes
                        return await interaction.response.send_message(
                            "The icon file must be 256 KB or smaller.",
                            ephemeral=True
                        )
                    
                    # Read the file content in memory
                    icon_data = await icon_file.read()
                    
                    # Ensure the file is a valid PNG (first 8 bytes check)
                    if len(icon_data) < 8 or not icon_data.startswith(b'\x89PNG\r\n\x1a\x0a'):
                        return await interaction.response.send_message(
                            "The file must be a valid PNG image.",
                            ephemeral=True
                        )
                    
                    # Check image dimensions using just the PNG header
                    # PNG width is bytes 16-19, height is bytes 20-23 of the IHDR chunk
                    if len(icon_data) >= 24:  # Minimum size for IHDR chunk
                        width = int.from_bytes(icon_data[16:20], byteorder='big')
                        height = int.from_bytes(icon_data[20:24], byteorder='big')
                        
                        if width < 64 or height < 64:
                            return await interaction.response.send_message(
                                "The icon must be at least 64x64 pixels in size.",
                                ephemeral=True
                            )
                    
                    # If we got here, the image is valid
                    changes['display_icon'] = icon_data
                except Exception as e:
                    return await interaction.response.send_message(
                        f"Failed to process the icon file: {str(e)}",
                        ephemeral=True
                    )
        elif icon_file is not None or remove_icon:
            return await interaction.response.send_message(
                "You don't have permission to change this role's icon.",
                ephemeral=True
            )
        
        if not changes:
            return await interaction.response.send_message(
                "No valid changes specified.",
                ephemeral=True
            )
        
        # Apply changes
        try:
            await role.edit(**changes, reason=f"Modified by: @{interaction.user.name} ({interaction.user.id})")
            
            # Build response message
            response_parts = [f"✅ Successfully updated {role.mention}:"]
            if 'name' in changes:
                response_parts.append(f"- Name: `{changes['name']}`")
            if 'color' in changes:
                if changes['color'] is None:
                    response_parts.append("- Color: Removed")
                else:
                    response_parts.append(f"- Color: `{str(changes['color'])}`")
            if 'icon' in changes:
                    response_parts.append("- Icon: Updated")
            
            await interaction.response.send_message("\n".join(response_parts), ephemeral=True)
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to modify this role. Please check my role hierarchy.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred while updating the role: {str(e)}",
                ephemeral=True
            )

async def setup(bot):
    # Create the wardrobe command group
    wardrobe_group = app_commands.Group(
        name="wardrobe", 
        description="Wardrobe commands", 
        default_permissions=discord.Permissions(administrator=True)
    )
    
    # Add the commands to the wardrobe group
    wardrobe_group.add_command(Wardrobe_cog(bot).setup_role)
    wardrobe_group.add_command(Wardrobe_cog(bot).delete_wardrobe_role)
    wardrobe_group.add_command(Wardrobe_cog(bot).list_wardrobe_roles)
    
    # Add the group to the bot's command tree
    bot.tree.add_command(wardrobe_group)
    bot.tree.add_command(Wardrobe_cog(bot).modify_role)
    