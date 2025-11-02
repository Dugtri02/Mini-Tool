import discord
from discord import app_commands
from discord.ext import commands
import asyncio

class Voice(commands.GroupCog, name="voice", description="Voice channel commands."):
    def __init__(self, bot):
        self.bot = bot
        super().__init__()

    # Move subgroup
    move = app_commands.Group(name="move", description="Voice channel move commands.")
    
    @move.command(name='gather', description='Moves specified users to a different voice channel')
    @app_commands.checks.has_permissions(move_members=True)
    async def moveusers(self, interaction: discord.Interaction, users: str, channel: discord.VoiceChannel):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).move_members:
            await interaction.response.send_message(f"❌ **You don't have permission to move members in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        if not channel.permissions_for(interaction.user).connect:
            await interaction.response.send_message(f"❌ **You don't have permission to connect to** {channel.mention}", ephemeral=True)
            return
        
        users = [discord.utils.get(interaction.guild.members, id=int(user_id.strip('<>@! '))) for user_id in users.split()]
        await interaction.response.send_message(f"✅ **Moving users** {' '.join([f'{user.mention}' for user in users])} **to** {channel.mention}", ephemeral=True)
        for user in users:
            if user.voice:
                if user.voice and interaction.user.voice.channel.permissions_for(interaction.user).move_members and interaction.user.voice.channel.permissions_for(user).connect and user.voice.channel.permissions_for(interaction.user).move_members and user.voice.channel.permissions_for(interaction.user).connect:
                    await user.move_to(channel)
                    await asyncio.sleep(1)
                else:
                    print('User not in voice channel')
            else:
                await asyncio.sleep(1)

    @move.command(name='all', description='Move up to 10 members or less of the voice channel you are in to the specified voice channel')
    @app_commands.checks.has_permissions(move_members=True)
    async def moveall(self, interaction: discord.Interaction, channel: discord.VoiceChannel):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not channel.permissions_for(interaction.user).connect:
            await interaction.response.send_message(f"❌ **You don't have permission to connect to** {channel.mention}", ephemeral=True)
            return
        if len(interaction.user.voice.channel.members) > 10:
            await interaction.response.send_message(f"❌ **Can only move up to 10 members or less**", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ **Moving all members of** {interaction.user.voice.channel.mention} **to** {channel.mention}", ephemeral=True)
        for member in interaction.user.voice.channel.members[:10]:
            await member.move_to(channel)
            await asyncio.sleep(1)

    @move.command(name='close', description='Disconnects all users from the voice channel you are in')
    @app_commands.checks.has_permissions(move_members=True)
    async def disconnectall(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).move_members:
            await interaction.response.send_message(f"❌ **You don't have permission to move members in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ **Disconnecting all users from** {interaction.user.voice.channel.mention}", ephemeral=True)
        try:
            for member in interaction.user.voice.channel.members:
                if member.id != interaction.user.id:
                    await member.move_to(None)
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(1)
            await interaction.user.move_to(None)
            await asyncio.sleep(1)
        except:
            return

    @move.command(name='purge', description='Disconnects all users without the move_members permission from the voice channel you are in')
    @app_commands.checks.has_permissions(move_members=True)
    async def disconnectall_noperms(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).move_members:
            await interaction.response.send_message(f"❌ **You don't have permission to move members in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ **Disconnecting all users without move_members from** {interaction.user.voice.channel.mention}", ephemeral=True)
        try:
            for member in interaction.user.voice.channel.members:
                if not member.guild_permissions.move_members:
                    await member.move_to(None)
                    await asyncio.sleep(1)
                else:
                    await asyncio.sleep(1)
        except:
            return
    
    # Self subgroup
    self_group = app_commands.Group(name="self", description="Voice channel self commands.")
    
    @self_group.command(name='deafen', description='Toggles your deafen status in the voice channel you are in')
    @app_commands.checks.has_permissions(deafen_members=True)
    async def toggled(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).deafen_members:
            await interaction.response.send_message(f"❌ **You don't have permission to deafen in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        if interaction.user.voice.deaf:
            await interaction.response.send_message(f"🔇 **Now listening in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=False)
        else:
            await interaction.response.send_message(f"🔊 **Now deafened in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=True)

    @self_group.command(name='toggle', description='Toggles both deafen and mute in the voice channel you are in')
    @app_commands.checks.has_permissions(deafen_members=True, mute_members=True)
    async def toggleall(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).deafen_members or not interaction.user.voice.channel.permissions_for(interaction.user).mute_members:
            await interaction.response.send_message(f"❌ **You don't have permission to both deafen and mute in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        if interaction.user.voice.deaf and interaction.user.voice.mute:
            await interaction.response.send_message(f"🔊🎤 **Now listening in and Server unmuted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=False, mute=False)
        elif interaction.user.voice.deaf and not interaction.user.voice.mute:
            await interaction.response.send_message(f"🔊🎤 **Now listening in and Server unmuted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=False, mute=True)
        elif not interaction.user.voice.deaf and interaction.user.voice.mute:
            await interaction.response.send_message(f"🔇🎤 **Now deafened and Server muted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=True, mute=False)
        else:
            await interaction.response.send_message(f"🔇🎤 **Now deafened and Server muted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(deafen=True, mute=True)

    @self_group.command(name='mute', description='Toggles the server mute in the voice channel you are in')
    @app_commands.checks.has_permissions(mute_members=True)
    async def togglem(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        if not interaction.user.voice.channel.permissions_for(interaction.user).mute_members:
            await interaction.response.send_message(f"❌ **You don't have permission to mute in** {interaction.user.voice.channel.mention}", ephemeral=True)
            return
        if interaction.user.voice.mute:
            await interaction.response.send_message(f"🔊 **Server unmuted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(mute=False)
        else:
            await interaction.response.send_message(f"🔇 **Server muted in** {interaction.user.voice.channel.mention}", ephemeral=True)
            await interaction.user.edit(mute=True)

    @self_group.command(name='disconnect', description='Disconnects you from the voice channel you are in')
    async def disconnect(self, interaction: discord.Interaction):
        if interaction.user.voice is None:
            await interaction.response.send_message(f"❌ **You are not in a voice channel**", ephemeral=True)
            return
        await interaction.response.send_message(f"✅ **Disconnecting from** {interaction.user.voice.channel.mention}", ephemeral=True)
        await interaction.user.move_to(None)

async def setup(bot):
    await bot.add_cog(Voice(bot))