import discord
from discord.ext import commands
from discord import app_commands

class BanSync(commands.GroupCog, name="ban_sync"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    def _create_tables(self):
        cursor = self.db.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ban_sync_links (
                guild_one_id BIGINT,
                guild_two_id BIGINT,
                PRIMARY KEY (guild_one_id, guild_two_id)
            )
        """)
        self.db.commit()

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

        if target_guild.owner_id != interaction.user.id:
            await interaction.followup.send("❌ You must be the owner of both guilds to link them.", ephemeral=True)
            return

        guild_one = min(interaction.guild.id, target_guild.id)
        guild_two = max(interaction.guild.id, target_guild.id)

        cursor = self.db.cursor()
        cursor.execute("SELECT 1 FROM ban_sync_links WHERE guild_one_id = ? AND guild_two_id = ?", (guild_one, guild_two))
        if cursor.fetchone():
            await interaction.followup.send("✅ These guilds are already linked.", ephemeral=True)
            return

        cursor.execute("INSERT INTO ban_sync_links (guild_one_id, guild_two_id) VALUES (?, ?)", (guild_one, guild_two))
        self.db.commit()

        await interaction.followup.send(f"✅ Successfully linked with guild `{target_guild.name}`.", ephemeral=True)

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
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target.id == user.id:
                    actor = entry.user
                    reason = entry.reason or "No reason provided"
                    break
        except discord.Forbidden:
            print(f"Missing Audit Log permissions in {guild.name} to fetch ban reason.")

        if actor and actor.id == self.bot.user.id:
            return

        linked_guilds = await self._get_linked_guilds(guild.id)
        sync_reason = f"{guild.name}: {reason}"

        for linked_guild_id in linked_guilds:
            linked_guild = self.bot.get_guild(linked_guild_id)
            if not linked_guild:
                continue

            try:
                await linked_guild.fetch_ban(user)
                # User is already banned, do nothing.
            except discord.NotFound:
                # User is not banned, proceed to ban.
                try:
                    member = await linked_guild.fetch_member(user.id)
                    if any([member.guild_permissions.administrator, member.guild_permissions.ban_members, member.guild_permissions.manage_guild, member.guild_permissions.kick_members]):
                        print(f"Skipping ban for {user.name} in {linked_guild.name} due to protected permissions.")
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
            print(f"Missing Audit Log permissions in {guild.name} to fetch unban reason.")

        if actor and actor.id == self.bot.user.id:
            return

        linked_guilds = await self._get_linked_guilds(guild.id)
        sync_reason = f"{guild.name}: {reason}"

        for linked_guild_id in linked_guilds:
            linked_guild = self.bot.get_guild(linked_guild_id)
            if not linked_guild:
                continue

            try:
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

async def setup(bot: commands.Bot):
    await bot.add_cog(BanSync(bot))
