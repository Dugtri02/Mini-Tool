import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button
from typing import Optional
import datetime

class BanSync(commands.GroupCog, name="sink"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    class BanButton(discord.ui.Button):
        def __init__(self, user_id: int, guild_id: int):
            super().__init__(style=discord.ButtonStyle.danger, label="Ban User", custom_id=f"ban_{user_id}_{guild_id}")
            self.user_id = user_id
            self.guild_id = guild_id

        async def callback(self, interaction: discord.Interaction):
            if not interaction.user.guild_permissions.ban_members:
                await interaction.response.send_message("❌ You don't have permission to use this button.", ephemeral=True)
                return

            guild = interaction.guild
            try:
                target_user = await interaction.client.fetch_user(self.user_id)
                target_member = await guild.fetch_member(target_user.id)
                
                # Check role hierarchy
                if target_member.top_role >= interaction.user.top_role:
                    await interaction.response.send_message(
                        f"❌ You can't ban {target_user.mention} because they have a higher or equal role than you.",
                        ephemeral=True
                    )
                    return
                    
            except discord.NotFound:
                # User not in guild, can proceed with ban
                pass
            except Exception as e:
                await interaction.response.send_message(f"❌ Error checking user permissions: {str(e)}", ephemeral=True)
                return
            
            try:
                await guild.ban(target_user, reason=f"Banned via ban sync alert (by {interaction.user})")
                await interaction.response.send_message(f"✅ Successfully banned {target_user.mention}.", ephemeral=True)
                
                # Update the embed to show the ban was completed
                embed = interaction.message.embeds[0]
                embed.color = discord.Color.green()
                embed.set_footer(text=f"Banned by {interaction.user}")
                await interaction.message.edit(embed=embed, view=None)
            except discord.Forbidden:
                await interaction.response.send_message("❌ I don't have permission to ban users in this server.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"❌ Failed to ban user: {str(e)}", ephemeral=True)

    def _create_tables(self):
        cursor = self.db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ban_sync_links (
                guild_one_id BIGINT,
                guild_two_id BIGINT,
                PRIMARY KEY (guild_one_id, guild_two_id)
            )
        """)
        
        # Create settings table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ban_sync_settings (
                guild_id BIGINT PRIMARY KEY,
                ban_alert_channel BIGINT
            )
        """)
            
        self.db.commit()

    async def _get_alert_channel(self, guild_id: int) -> Optional[discord.TextChannel]:
        """Get the alert channel for a guild if it exists."""
        cursor = self.db.cursor()
        cursor.execute("SELECT ban_alert_channel FROM ban_sync_settings WHERE guild_id = ?", (guild_id,))
        result = cursor.fetchone()
        if result and result[0]:
            channel = self.bot.get_channel(result[0])
            if channel and isinstance(channel, discord.TextChannel):
                return channel
        return None

    class GuildLinkRequestView(View):
        def __init__(self, source_guild: discord.Guild, target_guild: discord.Guild, db, requester: discord.Member, original_message: discord.Message = None):
            super().__init__(timeout=86400)  # 24 hours timeout
            self.source_guild = source_guild
            self.target_guild = target_guild
            self.db = db
            self.requester = requester
            self.original_message = original_message
            self.approved = asyncio.Event()
            self.decision = None  # 'accepted' or 'rejected'
            self.decided_by = None

        async def update_original_message(self, interaction: discord.Interaction = None):
            if not self.original_message:
                return
                
            if self.decision == 'accepted':
                embed = discord.Embed(
                    title="✅ Guild Link Request Approved",
                    description=f"**{self.source_guild.name}** is now linked to this server for ban synchronization.",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Approved by {self.decided_by}")
            else:  # rejected
                embed = discord.Embed(
                    title="❌ Guild Link Request Rejected",
                    description=f"The request to link with **{self.source_guild.name}** has been rejected.",
                    color=discord.Color.red()
                )
                embed.set_footer(text=f"Rejected by {self.decided_by}")
            
            try:
                if interaction and not interaction.response.is_done():
                    await interaction.response.edit_message(embed=embed, view=None)
                else:
                    await self.original_message.edit(embed=embed, view=None)
            except Exception as e:
                print(f"Error updating original message: {e}")

        async def notify_requester(self, interaction: discord.Interaction, approved: bool):
            try:
                # Try to send DM to the requester
                try:
                    if approved:
                        embed = discord.Embed(
                            title="✅ Guild Link Request Approved",
                            description=f"Your request to link with **{self.target_guild.name}** has been approved!",
                            color=discord.Color.green()
                        )
                    else:
                        embed = discord.Embed(
                            title="❌ Guild Link Request Rejected",
                            description=f"Your request to link with **{self.target_guild.name}** has been rejected.",
                            color=discord.Color.red()
                        )
                    
                    embed.set_footer(text=f"Decided by {interaction.user}")
                    await self.requester.send(embed=embed)
                except discord.Forbidden:
                    # If DM fails, try to send to the source guild's alert channel
                    alert_channel = await self._get_alert_channel(self.source_guild.id)
                    if alert_channel:
                        if approved:
                            message = f"✅ Your request to link with **{self.target_guild.name}** has been approved!"
                        else:
                            message = f"❌ Your request to link with **{self.target_guild.name}** has been rejected."
                        
                        try:
                            await alert_channel.send(f"{self.requester.mention} {message} (Decided by {interaction.user})")
                        except:
                            pass
            except Exception as e:
                print(f"Error notifying requester: {e}")

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
        async def accept(self, interaction: discord.Interaction, button: Button):
            if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
                await interaction.response.send_message("❌ You don't have permission to approve this request.", ephemeral=True)
                return

            await interaction.response.defer()
            self.decision = 'accepted'
            self.decided_by = str(interaction.user)
            await self.update_original_message(interaction)
            await self.notify_requester(interaction, approved=True)
            self.approved.set()
            await interaction.followup.send("✅ Guild link request approved!", ephemeral=True)
            self.stop()

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.red)
        async def reject(self, interaction: discord.Interaction, button: Button):
            if not interaction.user.guild_permissions.administrator and interaction.user.id != interaction.guild.owner_id:
                await interaction.response.send_message("❌ You don't have permission to reject this request.", ephemeral=True)
                return

            await interaction.response.defer()
            self.decision = 'rejected'
            self.decided_by = str(interaction.user)
            await self.update_original_message(interaction)
            await self.notify_requester(interaction, approved=False)
            await interaction.followup.send("❌ Guild link request rejected.", ephemeral=True)
            self.stop()

    @app_commands.command(name="add", description="Link another guild for ban synchronization.")
    @app_commands.describe(guild_id="The ID of the guild to link.")
    @app_commands.check(lambda interaction: interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id)
    async def add_link(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)

        try:
            target_guild_id = int(guild_id)
        except ValueError:
            await interaction.followup.send("❌ Invalid Guild ID format.", ephemeral=True)
            return

        if interaction.guild.id == target_guild_id:
            await interaction.followup.send("❌ You cannot link a guild to itself.", ephemeral=True)
            return

        target_guild = self.bot.get_guild(target_guild_id)
        if not target_guild:
            await interaction.followup.send("❌ I am not a member of the target guild.", ephemeral=True)
            return

        guild_one = min(interaction.guild.id, target_guild.id)
        guild_two = max(interaction.guild.id, target_guild.id)

        cursor = self.db.cursor()
        cursor.execute("SELECT 1 FROM ban_sync_links WHERE guild_one_id = ? AND guild_two_id = ?", (guild_one, guild_two))
        if cursor.fetchone():
            await interaction.followup.send("✅ These guilds are already linked.", ephemeral=True)
            return

        # Check if user has admin in target guild
        target_member = target_guild.get_member(interaction.user.id)
        has_admin_in_target = (target_member and 
                             (target_member.guild_permissions.administrator or 
                              target_member.id == target_guild.owner_id))

        if has_admin_in_target:
            # If user has admin in target guild, link directly
            cursor.execute("INSERT INTO ban_sync_links (guild_one_id, guild_two_id) VALUES (?, ?)", (guild_one, guild_two))
            self.db.commit()
            await interaction.followup.send(f"✅ Successfully linked with guild `{target_guild.name}`.", ephemeral=True)
        else:
            # If no admin in target guild, send a request to the target guild's alert channel
            alert_channel = await self._get_alert_channel(target_guild_id)
            if not alert_channel:
                await interaction.followup.send(
                    f"❌ You don't have administrator permissions in `{target_guild.name}` and they don't have an alert channel set up. "
                    "Please ask an administrator of that server to set up an alert channel using `/sink set_alert_channel`.",
                    ephemeral=True
                )
                return

            # Create and send the request
            embed = discord.Embed(
                title="🔗 Guild Link Request",
                description=(
                    f"**{interaction.guild.name}** wants to link with this server for ban synchronization.\n\n"
                    "⚠️ **This will allow them to sync bans to this server.**\n"
                    "Administrators of this server can accept or reject this request."
                ),
                color=discord.Color.orange()
            )
            embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
            embed.add_field(name="Requested by", value=f"{interaction.user.mention} (`{interaction.user.id}`)")
            
            try:
                # Create the view and store the message reference
                view = self.GuildLinkRequestView(
                    source_guild=interaction.guild,
                    target_guild=target_guild,
                    db=self.db,
                    requester=interaction.user
                )
                
                # Send the message and store the message reference in the view
                message = await alert_channel.send(embed=embed, view=view)
                view.original_message = message
                await interaction.followup.send(
                    f"📨 Sent a link request to `{target_guild.name}`. "
                    f"An administrator there needs to approve the request in {alert_channel.mention}.",
                    ephemeral=True
                )
                
                # Wait for the request to be approved or timeout
                try:
                    await asyncio.wait_for(view.approved.wait(), timeout=86400)  # 24 hours
                    
                    # If we get here, the request was approved
                    cursor.execute("INSERT INTO ban_sync_links (guild_one_id, guild_two_id) VALUES (?, ?)", (guild_one, guild_two))
                    self.db.commit()
                    
                    # Update the request message
                    embed.color = discord.Color.green()
                    embed.title = "✅ Guild Link Approved"
                    embed.description = f"**{interaction.guild.name}** is now linked to this server for ban synchronization."
                    await message.edit(embed=embed, view=None)
                    
                    # Notify the requester
                    await interaction.followup.send(
                        f"✅ Your request to link with `{target_guild.name}` has been approved! "
                        f"The guilds are now linked for ban synchronization.",
                        ephemeral=True
                    )
                    
                except asyncio.TimeoutError:
                    # Request timed out
                    embed.color = discord.Color.dark_grey()
                    embed.title = "⌛ Guild Link Request Expired"
                    await message.edit(embed=embed, view=None)
                    
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ I don't have permission to send messages in the alert channel of `{target_guild.name}`. "
                    "Please ask an administrator of that server to check my permissions.",
                    ephemeral=True
                )
            except Exception as e:
                await interaction.followup.send(
                    f"❌ An error occurred while sending the link request: {str(e)}",
                    ephemeral=True
                )

    @app_commands.command(name="remove", description="Unlink a guild from ban synchronization.")
    @app_commands.describe(guild_id="The ID of the guild to unlink.")
    @app_commands.check(lambda interaction: interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id)
    async def remove_link(self, interaction: discord.Interaction, guild_id: str):
        await interaction.response.defer(ephemeral=True)

        try:
            target_guild_id = int(guild_id)
        except ValueError:
            await interaction.followup.send("❌ Invalid Guild ID format.", ephemeral=True)
            return

        guild_one = min(interaction.guild.id, target_guild_id)
        guild_two = max(interaction.guild.id, target_guild_id)

        cursor = self.db.cursor()
        cursor.execute("DELETE FROM ban_sync_links WHERE guild_one_id = ? AND guild_two_id = ?", (guild_one, guild_two))
        deleted_rows = cursor.rowcount

        self.db.commit()

        if deleted_rows > 0:
            target_guild = self.bot.get_guild(target_guild_id)
            target_guild_name = target_guild.name if target_guild else guild_id
            await interaction.followup.send(f"✅ Successfully unlinked from guild `{target_guild_name}`.", ephemeral=True)
        else:
            await interaction.followup.send("❌ These guilds are not linked.", ephemeral=True)

    @app_commands.command(name="set_alert_channel", description="Set the channel where ban alerts will be sent.")
    @app_commands.describe(channel="The channel to send ban alerts to")
    @app_commands.check(lambda interaction: interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id)
    async def set_alert_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Set the channel where ban alerts will be sent."""
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO ban_sync_settings (guild_id, ban_alert_channel) VALUES (?, ?)",
            (interaction.guild.id, channel.id)
        )
        self.db.commit()
        await interaction.response.send_message(f"✅ Ban alerts will now be sent to {channel.mention}.", ephemeral=True)
    
    @app_commands.command(name="remove_alert_channel", description="Remove the channel where ban alerts will be sent.")
    @app_commands.check(lambda interaction: interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id)
    async def remove_alert_channel(self, interaction: discord.Interaction):
        cursor = self.db.cursor()
        cursor.execute("DELETE FROM ban_sync_settings WHERE guild_id = ?", (interaction.guild.id,))
        self.db.commit()
        await interaction.response.send_message("✅ Ban alerts will no longer be sent.", ephemeral=True)

    @app_commands.command(name="list", description="List all guilds linked for ban synchronization.")
    @app_commands.check(lambda interaction: interaction.user.guild_permissions.administrator or interaction.user.id == interaction.guild.owner_id)
    async def list_links(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        current_guild_id = interaction.guild.id

        cursor = self.db.cursor()
        cursor.execute("SELECT guild_one_id, guild_two_id FROM ban_sync_links WHERE guild_one_id = ? OR guild_two_id = ?", (current_guild_id, current_guild_id))
        links = cursor.fetchall()

        if not links:
            await interaction.followup.send("This guild is not linked with any others.", ephemeral=True)
            return

        linked_guild_ids = set()
        for g1, g2 in links:
            if g1 != current_guild_id:
                linked_guild_ids.add(g1)
            if g2 != current_guild_id:
                linked_guild_ids.add(g2)

        guild_list = []
        for guild_id in linked_guild_ids:
            guild = self.bot.get_guild(guild_id)
            if guild:
                has_perms = guild.me.guild_permissions.ban_members
                status = "✅ Can Ban" if has_perms else "❌ Can't Ban"
                guild_list.append(f"- {guild.name} (`{guild_id}`) - {status}")
            else:
                guild_list.append(f"- Unknown Guild (`{guild_id}`) - ❓ Bot Not In Server")

        embed = discord.Embed(title=f"Linked Guilds for `{interaction.guild.name}`", description="\n".join(guild_list), color=discord.Color.blue())
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _get_linked_guilds(self, guild_id: int) -> set[int]:
        cursor = self.db.cursor()
        cursor.execute("SELECT guild_one_id, guild_two_id FROM ban_sync_links WHERE guild_one_id = ? OR guild_two_id = ?", (guild_id, guild_id))
        links = cursor.fetchall()
        
        linked_ids = set()
        for g1, g2 in links:
            if g1 != guild_id:
                linked_ids.add(g1)
            if g2 != guild_id:
                linked_ids.add(g2)
        return linked_ids

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        actor = None
        reason = "No reason provided"
        
        # Try to find the ban entry in audit logs
        try:
            # Look for ban entries in the last 10 seconds
            async for entry in guild.audit_logs(
                limit=5,  # Check last 5 entries to be safe
                action=discord.AuditLogAction.ban,
                after=discord.utils.utcnow() - datetime.timedelta(seconds=10)
            ):
                if entry.target.id == user.id:
                    actor = entry.user
                    reason = entry.reason or "No reason provided"
                    break
        except discord.Forbidden:
            print(f"Missing Audit Log permissions in {guild.name} to fetch ban reason.")
        except Exception as e:
            print(f"Error fetching audit logs in {guild.name}: {e}")

        if actor is None:
            print(f"Could not find ban entry in audit logs for {user} in {guild.name}")
            actor_name = "[Unknown User]"
            actor_id = "[Unknown]"
            sync_reason = f"User banned in {guild.name}. Reason: {reason}"
        else:
            actor_name = actor.name
            actor_id = actor.id
            sync_reason = f"{actor_name} ({actor_id}) in {guild.name} banned for reason: {reason}"

        linked_guilds = await self._get_linked_guilds(guild.id)

        for linked_guild_id in linked_guilds:
            linked_guild = self.bot.get_guild(linked_guild_id)
            if not linked_guild:
                continue

            try:
                # Check if the actor has ban permissions and proper role hierarchy in the linked guild
                try:
                    actor_member = await linked_guild.fetch_member(actor.id)
                    
                    # Check if actor has ban permissions
                    if not actor_member.guild_permissions.ban_members:
                        await self._send_ban_alert(linked_guild, guild, actor, user, reason, "Missing Ban Permissions")
                        continue
                        
                    # Check if banned user is a bot or has higher role
                    try:
                        banned_member = await linked_guild.fetch_member(user.id)
                        alert_reason = None
                        
                        if banned_member.bot:
                            alert_reason = f"Cannot ban `{user.name}` - They are a bot account"
                        elif banned_member.top_role >= actor_member.top_role:
                            alert_reason = f"Cannot ban `{user.name}` - They have a higher or equal role"
                            
                        if alert_reason:
                            await self._send_ban_alert(
                                linked_guild, 
                                guild, 
                                actor, 
                                user, 
                                reason, 
                                alert_reason
                            )
                            continue
                    except discord.NotFound:
                        # User not in guild, can proceed with ban
                        pass
                        
                except discord.NotFound:
                    # Actor is not in the linked guild, send alert
                    await self._send_ban_alert(linked_guild, guild, actor, user, reason, "Actor not in guild")
                    continue
                except discord.HTTPException as e:
                    print(f"Error checking permissions in {linked_guild.name}: {e}")
                    continue

                await linked_guild.fetch_ban(user)
                # User is already banned, do nothing.
            except discord.NotFound:
                # User is not banned, proceed to ban.
                try:
                    member = await linked_guild.fetch_member(user.id)
                    if any([member.guild_permissions.administrator, member.guild_permissions.ban_members, member.guild_permissions.manage_guild, member.guild_permissions.kick_members]):
                        continue
                except discord.NotFound:
                    # Member not in guild, can be banned.
                    pass
                except discord.HTTPException as e:
                    print(f"Failed to fetch member {user.id} from {linked_guild.name}: {e}")
                    continue

                try:
                    await linked_guild.ban(user, reason=sync_reason)
                except discord.Forbidden:
                    print(f"Failed to sync ban for {user.id} to {linked_guild.name}: Missing Permissions")
                except discord.HTTPException as e:
                    print(f"Failed to sync ban for {user.id} to {linked_guild.name}: {e}")
            except discord.Forbidden:
                print(f"Failed to check ban status for {user.id} in {linked_guild.name}: Missing Permissions")
            except discord.HTTPException as e:
                print(f"Failed to check ban status for {user.id} in {linked_guild.name}: {e}")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        actor = None
        reason = "No reason provided"
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.unban):
                if entry.target.id == user.id:
                    actor = entry.user
                    reason = entry.reason or "No reason provided"
                    break
        except discord.Forbidden:
            pass

        if actor and actor.id == self.bot.user.id:
            return

        linked_guilds = await self._get_linked_guilds(guild.id)
        sync_reason = f"{guild.name}: {reason}"

        for linked_guild_id in linked_guilds:
            linked_guild = self.bot.get_guild(linked_guild_id)
            if not linked_guild:
                continue

            try:
                # Check if the actor has ban permissions in the linked guild
                try:
                    actor_member = await linked_guild.fetch_member(actor.id)
                    if not actor_member.guild_permissions.ban_members:
                        continue
                except discord.NotFound:
                    continue
                except discord.HTTPException as e:
                    print(f"Error checking permissions in {linked_guild.name}: {e}")
                    continue

                await linked_guild.fetch_ban(user)
                # User is banned, proceed to unban.
                try:
                    await linked_guild.unban(user, reason=sync_reason)
                except discord.Forbidden:
                    print(f"Failed to sync unban for {user.id} to {linked_guild.name}: Missing Permissions")
                except discord.HTTPException as e:
                    print(f"Failed to sync unban for {user.id} to {linked_guild.name}: {e}")
            except discord.NotFound:
                # User is not banned, do nothing.
                pass
            except discord.Forbidden:
                print(f"Failed to check ban status for {user.id} in {linked_guild.name}: Missing Permissions")
            except discord.HTTPException as e:
                print(f"Failed to check ban status for {user.id} in {linked_guild.name}: {e}")

    @commands.Cog.listener()
    async def _send_ban_alert(self, target_guild: discord.Guild, source_guild: discord.Guild, 
                            actor: discord.Member, user: discord.User, reason: str, 
                            alert_reason: str = "No reason provided"):
        """Send a ban alert to the configured ban alert channel.
        
        Args:
            target_guild: The guild where the alert should be sent
            source_guild: The guild where the ban originated
            actor: The member who performed the ban
            user: The user who was banned
            reason: The reason for the ban
            alert_reason: The reason for the alert (why the ban couldn't be auto-synced)
        """
        cursor = self.db.cursor()
        cursor.execute("SELECT ban_alert_channel FROM ban_sync_settings WHERE guild_id = ?", (target_guild.id,))
        result = cursor.fetchone()
        
        if not result or not result[0]:
            return
            
        channel = target_guild.get_channel(result[0])
        if not channel:
            return
            
        embed = discord.Embed(
            title="⚠️ Ban Sync Alert",
            description=(
                f"> User banned in `{source_guild.name}`.\n"
                f"> {alert_reason}\n\n"
                f"**User:** {user.mention} (`{user.id}`)\n"
                f"**Banned by:** {actor.mention} (`{actor.id}`)\n"
                f"**Reason:** {reason or 'No reason provided'}"
            ),
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        view = View(timeout=None)
        view.add_item(self.BanButton(user.id, target_guild.id))
        
        try:
            await channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Failed to send ban alert in {target_guild.name}: {e}")

    async def on_command_error(self, interaction: discord.Interaction, error: Exception) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "❌ You don't have permission to use this command. You need to be an administrator or the server owner.",
                ephemeral=True
            )
        else:
            # Re-raise the error if it's not a CheckFailure
            raise error

async def setup(bot: commands.Bot):
    cog = BanSync(bot)
    bot.tree.on_error = cog.on_command_error  # Register the error handler
    await bot.add_cog(cog)