import discord
from discord.ext import commands
from discord import app_commands

class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="help", description="information about the bot.")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Beta v0.2",
            description="This is a beta version of the bot so the help command is not yet fully implemented.\nAsk for help in the [support server](https://discord.gg/exwPCtMEsD).\n\n⚠️The bot is in active development and may be unstable, unreliable and potentially exploitable... use with caution ⚠️"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
async def setup(bot):
    await bot.add_cog(Misc(bot))