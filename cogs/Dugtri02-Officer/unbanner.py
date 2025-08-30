import discord
from discord import app_commands, ui
from discord.ext import commands
import asyncio
from typing import Dict, Any, Optional

class BanPurgeView(ui.View):
    def __init__(self, purger):
        super().__init__(timeout=None)
        self.purger = purger
        self.stop_requested = False
    
    @ui.button(label="⏹️ Stop Purge", style=discord.ButtonStyle.danger, custom_id="stop_purge")
    async def stop_purge(self, interaction: discord.Interaction, button: ui.Button):
        self.stop_requested = True
        button.disabled = True
        button.label = "🛑 Stopping..."
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🛑 Stop requested. The purge will complete the current batch and then stop.", ephemeral=True)

class BanPurger(commands.GroupCog, name="unbans"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.purge_queue = asyncio.Queue()
        self.processing_purge = False
        self.active_purges: Dict[int, Dict[str, Any]] = {}  # guild_id: {view, task}

    async def process_purge_queue(self):
        if self.processing_purge:
            return
            
        self.processing_purge = True
        try:
            while not self.purge_queue.empty():
                interaction, guild_id = await self.purge_queue.get()
                await self._process_purge(interaction, guild_id)
                self.purge_queue.task_done()
        finally:
            self.processing_purge = False

    async def _process_purge(self, interaction: discord.Interaction, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        # Check if user is still guild owner
        if interaction.user.id != guild.owner_id:
            await interaction.followup.send("❌ Only the server owner can initiate a ban purge.", ephemeral=True)
            return

        view = BanPurgeView(self)
        message = await interaction.followup.send(
            f"🔄 Starting ban list purge for **{guild.name}**...",
            view=view,
            wait=True,
            ephemeral=True
        )

        self.active_purges[guild_id] = {
            "view": view,
            "message": message
        }

        try:
            bans = [entry async for entry in guild.bans()]
            total_bans = len(bans)
            processed = 0
            failed = 0
            progress = 0.0  # Initialize progress variable

            for ban in bans:
                if view.stop_requested:
                    break

                try:
                    await guild.unban(ban.user, reason="Ban list purge")
                    processed += 1
                    if processed % 10 == 0:  # Update progress every 10 unbans
                        progress = (processed / total_bans * 100) if total_bans > 0 else 0
                    await message.edit(
                        content=(
                            f"⏳ Purging ban list for **{guild.name}**... {progress:.1f}%\n"
                            f"✅ Unbanned: {processed} | ❌ Failed: {failed} | 📊 Total: {total_bans}"
                        ),
                        view=view
                    )
                except Exception as e:
                    failed += 1

            status = "completed" if not view.stop_requested else "stopped"
            await message.edit(
                content=(
                    f"✅ Ban purge {status} for **{guild.name}**\n"
                    f"✅ Unbanned: {processed} | ❌ Failed: {failed} | 📊 Total: {total_bans}"
                ),
                view=None
            )

        except Exception as e:
            await message.edit(
                content=f"❌ An error occurred during ban purge: {str(e)}",
                view=None
            )
        finally:
            self.active_purges.pop(guild_id, None)

    @app_commands.command(name="purge", description="Purge all bans from this server (Owner only)")
    async def purge_bans(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)

        if interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message("❌ Only the server owner can use this command.", ephemeral=True)

        await interaction.response.defer(thinking=True, ephemeral=True)

        # Add to queue
        await self.purge_queue.put((interaction, interaction.guild.id))
        await interaction.followup.send("✅ Ban purge request added to queue. Processing will start soon...", ephemeral=True)
        await self.process_purge_queue()

async def setup(bot: commands.Bot):
    await bot.add_cog(BanPurger(bot))