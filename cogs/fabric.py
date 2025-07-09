import discord
import sqlite3
from discord.ext import commands
from discord import app_commands
from typing import List, Set

class RoleManager(commands.GroupCog, name="role"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    def _create_tables(self):
        """Create necessary database tables for role management."""
        cursor = self.db.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS minirole (
            guild_id INTEGER,
            authorizedid INTEGER,
            optionid INTEGER,
            PRIMARY KEY (guild_id, authorizedid, optionid)
        )
        ''')
        self.db.commit()
    
    async def get_authorized_roles(self, user: discord.Member, guild_id: int) -> List[discord.Role]:
        """Get all roles that the user is authorized to manage."""
        cursor = self.db.cursor()
        cursor.execute('''
            SELECT DISTINCT optionid 
            FROM minirole 
            WHERE guild_id = ? AND authorizedid IN ({})
        '''.format(','.join('?' for _ in user.roles)), 
        (guild_id, *[role.id for role in user.roles]))
        
        role_ids = {row[0] for row in cursor.fetchall()}
        return [role for role in user.guild.roles if role.id in role_ids]

    async def role_autocomplete(
        self, 
        interaction: discord.Interaction, 
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for roles that the user can manage."""
        if not interaction.guild:
            return []
            
        authorized_roles = await self.get_authorized_roles(interaction.user, interaction.guild.id)
        if not authorized_roles:
            return []
            
        role_choices = []
        for role in authorized_roles:
            if current.lower() in role.name.lower():
                role_choices.append(
                    app_commands.Choice(name=role.name, value=str(role.id))
                )
        return role_choices[:25]

    @app_commands.command(name="give", description="Give a role to a member. (config: /fabric setup)")
    @app_commands.describe(
        member="The member to give the role to",
        role="The role to give"
    )
    @app_commands.autocomplete(role=role_autocomplete)
    async def role_give(
        self, 
        interaction: discord.Interaction, 
        member: discord.Member,
        role: str
    ):
        """Give a role to a member if you have permission."""
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        
        if interaction.user == member:
            return await interaction.response.send_message("You cannot give yourself a role.", ephemeral=True)

        try:
            # Check if target is a bot
            if member.bot:
                return await interaction.response.send_message(
                    "❌ You cannot modify roles for bots.",
                    ephemeral=True
                )
                
            # Check if target has manage_roles permission
            if member.guild_permissions.manage_roles:
                return await interaction.response.send_message(
                    "❌ You cannot modify roles for users with role management permissions.",
                    ephemeral=True
                )
                
            role_id = int(role)
            role_obj = interaction.guild.get_role(role_id)
            if not role_obj:
                return await interaction.response.send_message("❌ Role not found.", ephemeral=True)
                
            # Check if user can manage this role
            authorized_roles = await self.get_authorized_roles(interaction.user, interaction.guild.id)
            if role_obj not in authorized_roles:
                return await interaction.response.send_message(
                    "❌ You don't have permission to manage this role.", 
                    ephemeral=True
                )
                
            await member.add_roles(role_obj)
            await interaction.response.send_message(
                f"✅ Added {role_obj.mention} to {member.mention}", 
                ephemeral=True
            )
            
        except ValueError:
            await interaction.response.send_message("Invalid role ID.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to add that role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
    
    @app_commands.command(name="remove", description="Remove a role from a member. (config: /fabric setup)")
    @app_commands.describe(
        member="The member to remove the role from",
        role="The role to remove"
    )
    @app_commands.autocomplete(role=role_autocomplete)
    async def role_remove(
        self, 
        interaction: discord.Interaction, 
        member: discord.Member,
        role: str
    ):
        """Remove a role from a member if you have permission."""
        if not interaction.guild:
            return await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        
        if interaction.user == member:
            return await interaction.response.send_message("You cannot remove yourself from a role.", ephemeral=True)

        try:
            # Check if target is a bot
            if member.bot:
                return await interaction.response.send_message(
                    "❌ You cannot modify roles for bots.",
                    ephemeral=True
                )
                
            # Check if target has manage_roles permission
            if member.guild_permissions.manage_roles:
                return await interaction.response.send_message(
                    "❌ You cannot modify roles for users with role management permissions.",
                    ephemeral=True
                )
                
            role_id = int(role)
            role_obj = interaction.guild.get_role(role_id)
            if not role_obj:
                return await interaction.response.send_message("❌ Role not found.", ephemeral=True)
                
            # Check if user can manage this role
            authorized_roles = await self.get_authorized_roles(interaction.user, interaction.guild.id)
            if role_obj not in authorized_roles:
                return await interaction.response.send_message(
                    "❌ You don't have permission to manage this role.", 
                    ephemeral=True
                )
                
            # Check if member has the role
            if role_obj not in member.roles:
                return await interaction.response.send_message(
                    f"❌ {member.mention} doesn't have the {role_obj.mention} role.",
                    ephemeral=True
                )
                
            await member.remove_roles(role_obj)
            await interaction.response.send_message(
                f"✅ Removed {role_obj.mention} from {member.mention}", 
                ephemeral=True
            )
            
        except ValueError:
            await interaction.response.send_message("Invalid role ID.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to remove that role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
    
class Fabric(commands.GroupCog, name="fabric"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        try:
            """Clean up minirole table when a role is deleted"""
            cursor = self.db.cursor()
            
            # Check if the role was even in the database
            cursor.execute('''
                SELECT COUNT(*) 
                FROM minirole 
                WHERE guild_id = ? AND (authorizedid = ? OR optionid = ?)
            ''', (role.guild.id, role.id, role.id))
            
            if cursor.fetchone()[0] == 0:
                return  # Role wasn't in the database, nothing to do
            
            # Remove any entries where the deleted role was a manager or a managed role
            cursor.execute('''
                DELETE FROM minirole 
                WHERE guild_id = ? AND (authorizedid = ? OR optionid = ?)
            ''', (role.guild.id, role.id, role.id))
            
            self.db.commit()
        except Exception as e:
            print(f"Error cleaning up minirole table: {e}")
    
    def _create_tables(self):
        """Create necessary database tables for role management."""
        cursor = self.db.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS minirole (
            guild_id INTEGER,
            authorizedid INTEGER,
            optionid INTEGER,
            PRIMARY KEY (guild_id, authorizedid, optionid)
        )
        ''')
        self.db.commit()
    
    async def get_authorized_roles(self, user: discord.Member, guild_id: int) -> List[discord.Role]:
        """Get all roles that the user is authorized to manage."""
        cursor = self.db.cursor()
        cursor.execute('''
            SELECT DISTINCT optionid 
            FROM minirole 
            WHERE guild_id = ? AND authorizedid IN ({})
        '''.format(','.join('?' for _ in user.roles)), 
        (guild_id, *[role.id for role in user.roles]))
        
        role_ids = {row[0] for row in cursor.fetchall()}
        return [role for role in user.guild.roles if role.id in role_ids]

    async def role_autocomplete(
        self, 
        interaction: discord.Interaction, 
        current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for roles that the user can manage."""
        if not interaction.guild:
            return []
            
        authorized_roles = await self.get_authorized_roles(interaction.user, interaction.guild.id)
        if not authorized_roles:
            return []
            
        role_choices = []
        for role in authorized_roles:
            if current.lower() in role.name.lower():
                role_choices.append(
                    app_commands.Choice(name=role.name, value=str(role.id))
                )
        return role_choices[:25]
    
    @app_commands.command(name="setup", description="Manage role permissions. (for: /role add|remove)")
    @app_commands.describe(
        manager="The role that can manage other roles",
        add="Role to add to management (optional)",
        remove="Role to remove from management (optional)",
        delete="Set to True to remove all permissions for manager",
        can_edit="Set to True to allow for functionality with Wardrobe"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def role_setup(
        self,
        interaction: discord.Interaction,
        manager: discord.Role,
        add: discord.Role = None,
        remove: discord.Role = None,
        delete: bool = False,
        can_edit: bool = False
    ):
        """
        Manage role permissions:
        - /role-setup manager:@Role add:@Role - Add management permission
        - /role-setup manager:@Role remove:@Role - Remove management permission
        - /role-setup manager:@Role delete:True - Remove all permissions for manager
        """
        try:
            cursor = self.db.cursor()
            
            if delete:
                # Remove all permissions for the manager
                cursor.execute('''
                    DELETE FROM minirole 
                    WHERE guild_id = ? AND authorizedid = ?
                ''', (interaction.guild_id, manager.id))
                self.db.commit()
                return await interaction.response.send_message(
                    f"✅ Removed all role management permissions for {manager.mention}",
                    ephemeral=True
                )
                
            if add and remove:
                return await interaction.response.send_message(
                    "❌ Please specify either 'add' or 'remove', not both.",
                    ephemeral=True
                )
                
            if add:
                # Check if the role to be added is a bot role
                if add.is_bot_managed():
                    return await interaction.response.send_message(
                        "❌ You cannot manage bot roles with this command.",
                        ephemeral=True
                    )
                    
                # Add management permission
                cursor.execute('''
                    INSERT OR IGNORE INTO minirole (guild_id, authorizedid, optionid)
                    VALUES (?, ?, ?)
                ''', (interaction.guild_id, manager.id, add.id))
                
                # Check if the target role has a wardrobe configuration and can_edit is True
                if can_edit:
                    cursor.execute('''
                        SELECT 1 FROM wardrobe_roles 
                        WHERE guild_id = ? AND role_id = ?
                    ''', (interaction.guild_id, add.id))
                    
                    if cursor.fetchone():
                        # Update the created_by_role_id in wardrobe_roles
                        cursor.execute('''
                            UPDATE wardrobe_roles 
                            SET created_by_role_id = ?
                            WHERE guild_id = ? AND role_id = ?
                        ''', (manager.id, interaction.guild_id, add.id))
                
                self.db.commit()
                return await interaction.response.send_message(
                    f"✅ {manager.mention} can now manage {add.mention}",
                    ephemeral=True
                )
                
            if remove:
                # Check if the role to be removed is a bot role
                if remove.is_bot_managed():
                    return await interaction.response.send_message(
                        "❌ You cannot manage bot roles with this command.",
                        ephemeral=True
                    )
                    
                # Remove specific management permission
                cursor.execute('''
                    DELETE FROM minirole 
                    WHERE guild_id = ? AND authorizedid = ? AND optionid = ?
                ''', (interaction.guild_id, manager.id, remove.id))
                self.db.commit()
                return await interaction.response.send_message(
                    f"✅ Removed permission for {manager.mention} to manage {remove.mention}",
                    ephemeral=True
                )
                
            # If no action specified, show current permissions
            cursor.execute('''
                SELECT optionid FROM minirole 
                WHERE guild_id = ? AND authorizedid = ?
            ''', (interaction.guild_id, manager.id))
            
            managed_roles = [
                interaction.guild.get_role(row[0])
                for row in cursor.fetchall()
                if interaction.guild.get_role(row[0])  # Only include valid roles
            ]
            
            if not managed_roles:
                return await interaction.response.send_message(
                    f"❌ {manager.mention} doesn't have any role management permissions.",
                    ephemeral=True
                )
                
            embed = discord.Embed(
                title=f"Role Management Permissions for {manager.name}",
                color=discord.Color.blue()
            )
            embed.description = '\n'.join(
                f"• {role.mention} (ID: {role.id})"
                for role in managed_roles
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to update role management: {e}",
                ephemeral=True
            )

    @app_commands.command(name="list", description="View role management permissions. (for: /role add|remove)")
    @app_commands.describe(
        role="(Optional) The role to view permissions for",
        page="Page number to view (for pagination)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def role_list(self, interaction: discord.Interaction, role: discord.Role = None, page: int = 1):
        """View role management permissions. Shows all roles with permissions if no role is specified."""
        cursor = self.db.cursor()
        
        if role:
            # Show detailed permissions for a specific role
            cursor.execute('''
                SELECT optionid 
                FROM minirole 
                WHERE guild_id = ? AND authorizedid = ?
                ORDER BY optionid
            ''', (interaction.guild_id, role.id))
            
            managed_roles = [
                interaction.guild.get_role(row[0])
                for row in cursor.fetchall()
                if interaction.guild.get_role(row[0])  # Only include valid roles
            ]
            
            if not managed_roles:
                return await interaction.response.send_message(
                    f"❌ {role.mention} doesn't have any role management permissions.",
                    ephemeral=True
                )
            
            # Split managed roles into pages
            ROLES_PER_PAGE = 8
            pages = [managed_roles[i:i + ROLES_PER_PAGE] for i in range(0, len(managed_roles), ROLES_PER_PAGE)]
            
            # Adjust page number to 0-based index and validate
            page = max(1, min(page, len(pages)))
            current_page = page - 1
            page_roles = pages[current_page]
            
            # Create embed for specific role
            embed = discord.Embed(
                title=f"Fabric Overview for \"{role.name}\" 📝",
                description=f"Page {page} of {len(pages)}",
                color=discord.Color.blue()
            )
            
            # Add roles for current page
            embed.add_field(
                name=f"Can manage the following roles:",
                value='\n'.join(f"- {role.mention}" for role in page_roles) or "No roles found",
                inline=False
            )
            
            # Add pagination footer if there are multiple pages
            if len(pages) > 1:
                embed.set_footer(text=f"Use `/fabric list role:{role.name} page:<number>` to view other pages")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # If no role specified, show all roles with management options
        cursor.execute('''
            SELECT DISTINCT authorizedid
            FROM minirole 
            WHERE guild_id = ?
            ORDER BY authorizedid
        ''', (interaction.guild_id,))
        
        managers = [
            interaction.guild.get_role(row[0])
            for row in cursor.fetchall()
            if interaction.guild.get_role(row[0])  # Only include valid roles
        ]
        
        if not managers:
            return await interaction.response.send_message(
                "❌ No role management permissions have been set up yet.",
                ephemeral=True
            )
        
        # Split admin roles into pages (10 roles per page)
        ROLES_PER_PAGE = 8
        pages = [managers[i:i + ROLES_PER_PAGE] for i in range(0, len(managers), ROLES_PER_PAGE)]
        
        # Adjust page number to 0-based index and validate
        page = max(1, min(page, len(pages)))
        current_page = page - 1
        page_roles = pages[current_page]
        
        # Create embed for all roles
        embed = discord.Embed(
            title="Fabric Server Overview 📋",
            description=f"Page {page} of {len(pages)}\n\nRoles with management permissions:",
            color=discord.Color.blue()
        )
        
        # Add roles for current page with role counts
        for manager in page_roles:
            cursor.execute('''
                SELECT COUNT(*) 
                FROM minirole 
                WHERE guild_id = ? AND authorizedid = ?
            ''', (interaction.guild_id, manager.id))
            role_count = cursor.fetchone()[0]
            
            # Use role ID in the name and mention in the value for proper display
            embed.add_field(
                name=f"Role ID: {manager.id}",
                value=f"{manager.mention}\nManages {role_count} role{'s' if role_count != 1 else ''}",
                inline=True
            )
        
        # Add pagination footer if there are multiple pages
        if len(pages) > 1:
            embed.set_footer(text=f"Use `/fabric list page:<number>` to view other pages")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Fabric(bot))
    await bot.add_cog(RoleManager(bot))