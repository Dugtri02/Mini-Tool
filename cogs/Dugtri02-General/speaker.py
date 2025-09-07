import discord
from discord import app_commands
from discord.ext import commands

class Speaker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Slash command checks
    def slash_is_owner():
        """Check if the user is the bot owner (for slash commands)"""
        async def predicate(interaction: discord.Interaction) -> bool:
            return await interaction.client.is_owner(interaction.user)
        return app_commands.check(predicate)

    @app_commands.command(name="say", description="Make the bot say something (edit|delete|format)")
    @app_commands.describe(
        message="The message to send. ({ln} for newlines)",
        reply_to="(Optional) Message ID or URL to reply to",
        channel="(Optional) Channel ID to send the message to",
        suppress_embeds="(Optional) Whether to suppress embeds in the message (true/false)"
    )
    @slash_is_owner()
    async def say(self,
        interaction: discord.Interaction,
        message: str,
        reply_to: str = None,
        reply_mention_author: bool = True,
        channel: str = None,
        suppress_embeds: bool = False
    ):
        """Send a message as the bot with various options"""
        # Always defer as ephemeral to avoid timeout issues
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            # Check if both reply_to and channel are provided
            if reply_to and channel:
                await interaction.followup.send(
                    "❌ Cannot use both reply_to and channel parameters together. "
                    "Reply is only allowed in the current channel.",
                    ephemeral=True
                )
                return

            # Get the target channel
            target_channel = await get_channel(interaction, channel)
            if not target_channel:
                return

            # Convert {ln} to newlines in the message
            formatted_message = message.replace('{ln}', '\n')
            formatted_message = formatted_message.replace('{server}', f'{interaction.guild.name}')
            formatted_message = formatted_message.replace('{servers}', f'{len(self.bot.guilds)}')
            formatted_message = formatted_message.replace('{members}', f'{interaction.guild.member_count}')
            formatted_message = formatted_message.replace('{bots}', f'{len([m for m in interaction.guild.members if m.bot])}')
            formatted_message = formatted_message.replace('{users}', f'{interaction.guild.member_count - len([m for m in interaction.guild.members if m.bot])}')
            formatted_message = formatted_message.replace('{channels}', f'{len(interaction.guild.text_channels)}')
            formatted_message = formatted_message.replace('{voice}', f'{len(interaction.guild.voice_channels)}')
            formatted_message = formatted_message.replace('{categories}', f'{len(interaction.guild.categories)}')
            formatted_message = formatted_message.replace('{stage}', f'{len(interaction.guild.stage_channels)}')
            if interaction.guild.rules_channel:
                formatted_message = formatted_message.replace('{rules}', f'{interaction.guild.rules_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{rules}', 'rules')
            if interaction.guild.afk_channel:
                formatted_message = formatted_message.replace('{afk}', f'{interaction.guild.afk_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{afk}', 'afk')
            if interaction.guild.system_channel:
                formatted_message = formatted_message.replace('{system}', f'{interaction.guild.system_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{system}', 'system')
            formatted_message = formatted_message.replace('{forums}', f'{len(interaction.guild.forums)}')
            formatted_message = formatted_message.replace('{roles}', f'{len(interaction.guild.roles)}')
            formatted_message = formatted_message.replace('{emojis}', f'{len(interaction.guild.emojis)}')
            formatted_message = formatted_message.replace('{boosts}', f'{interaction.guild.premium_subscription_count}')
            formatted_message = formatted_message.replace('{boosters}', f'{len([m for m in interaction.guild.members if m.premium_since])}')
            formatted_message = formatted_message.replace('{owner}', f'{interaction.guild.owner}')
            formatted_message = formatted_message.replace('{@owner}', f'{interaction.guild.owner.mention}')

            # Prepare the send kwargs
            send_kwargs = {}
            if suppress_embeds:
                send_kwargs['suppress_embeds'] = True
            
            target_message = None
            if reply_to:
                # If we're in a different channel, don't allow reply_to
                if target_channel.id != interaction.channel.id:
                    await interaction.followup.send(
                        "❌ Cannot reply to a message in a different channel. "
                        "Please remove the channel parameter to reply in the current channel.",
                        ephemeral=True
                    )
                    return
                    
                target_message = await extract_message_id(interaction, reply_to)
                if not target_message:  # Error already handled in extract_message_id
                    return
            
            if target_message:
                sent_message = await target_message.reply(
                    formatted_message,
                    mention_author=reply_mention_author,
                    **send_kwargs
                )
                await interaction.followup.send(
                    f"✅ Replied to [message]({target_message.jump_url})",
                    ephemeral=True
                )
            else:
                sent_message = await target_channel.send(formatted_message, **send_kwargs)
                await interaction.followup.send(
                    f"✅ Message sent! [Jump to message]({sent_message.jump_url})",
                    ephemeral=True
                )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to send message: {str(e)}", ephemeral=True)

    @app_commands.command(name="edit", description="Edit a message (say|delete|format)")
    @app_commands.describe(
        message_ref="The message ID or URL to edit",
        new_content="The new content for the message. ({ln} for newlines)"
    )
    @slash_is_owner()
    async def edit(self, interaction: discord.Interaction, message_ref: str, new_content: str):
        """Edit a message as the bot"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            # Reuse the extract_message_id function to get the message
            message = await extract_message_id(interaction, message_ref)
            if not message:
                return  # Error already handled in extract_message_id
                
            # Check if the bot can edit the message
            if message.author != interaction.guild.me:
                await interaction.followup.send("❌ I can only edit my own messages.", ephemeral=True)
                return
            
            # Get the message content and apply replacements
            formatted_message = new_content
            formatted_message = formatted_message.replace('{ln}', '\n')
            formatted_message = formatted_message.replace('{server}', f'{interaction.guild.name}')
            formatted_message = formatted_message.replace('{servers}', f'{len(self.bot.guilds)}')
            formatted_message = formatted_message.replace('{members}', f'{interaction.guild.member_count}')
            formatted_message = formatted_message.replace('{bots}', f'{len([m for m in interaction.guild.members if m.bot])}')
            formatted_message = formatted_message.replace('{users}', f'{interaction.guild.member_count - len([m for m in interaction.guild.members if m.bot])}')
            formatted_message = formatted_message.replace('{channels}', f'{len(interaction.guild.text_channels)}')
            formatted_message = formatted_message.replace('{voice}', f'{len(interaction.guild.voice_channels)}')
            formatted_message = formatted_message.replace('{categories}', f'{len(interaction.guild.categories)}')
            formatted_message = formatted_message.replace('{stage}', f'{len(interaction.guild.stage_channels)}')
            if interaction.guild.rules_channel:
                formatted_message = formatted_message.replace('{rules}', f'{interaction.guild.rules_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{rules}', 'rules')
            if interaction.guild.afk_channel:
                formatted_message = formatted_message.replace('{afk}', f'{interaction.guild.afk_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{afk}', 'afk')
            if interaction.guild.system_channel:
                formatted_message = formatted_message.replace('{system}', f'{interaction.guild.system_channel.mention}')
            else:
                formatted_message = formatted_message.replace('{system}', 'system')
            formatted_message = formatted_message.replace('{forums}', f'{len(interaction.guild.forums)}')
            formatted_message = formatted_message.replace('{roles}', f'{len(interaction.guild.roles)}')
            formatted_message = formatted_message.replace('{emojis}', f'{len(interaction.guild.emojis)}')
            formatted_message = formatted_message.replace('{boosts}', f'{interaction.guild.premium_subscription_count}')
            formatted_message = formatted_message.replace('{boosters}', f'{len([m for m in interaction.guild.members if m.premium_since])}')
            formatted_message = formatted_message.replace('{owner}', f'{interaction.guild.owner}')
            formatted_message = formatted_message.replace('{@owner}', f'{interaction.guild.owner.mention}')
                
            await message.edit(content=formatted_message)
            await interaction.followup.send(
                f"✅ Message edited! [Jump to message]({message.jump_url})",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to edit message: {str(e)}", ephemeral=True)

    @app_commands.command(name="delete", description="Delete a message (say|edit|format)")
    @app_commands.describe(
        message_ref="The message ID or URL to delete"
    )
    @slash_is_owner()
    async def delete(self, interaction: discord.Interaction, message_ref: str):
        """Delete a message as the bot"""
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            # Reuse the extract_message_id function to get the message
            message = await extract_message_id(interaction, message_ref)
            if not message:
                return  # Error already handled in extract_message_id
                
            # Check if the bot can delete the message
            if message.author != interaction.guild.me:
                await interaction.followup.send("❌ I can only delete my own messages.", ephemeral=True)
                return
                
            await message.delete()
            await interaction.followup.send(
                f"✅ Message deleted!",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete message: {str(e)}", ephemeral=True)

    @app_commands.command(name="format", description="Get a list of say|edit formatting options (say|edit|delete)")
    @slash_is_owner()
    async def formatting(self, interaction: discord.Interaction):
        description = """Here are the options for </say:1376682426662912153> | </edit:1376684674616787047>

    `{ln}` - Newline
    `{server}` - Server name
    `{servers}` - Number of servers
    `{members}` - Number of members
    `{bots}` - Number of bots
    `{users}` - Number of users
    `{channels}` - Number of channels
    `{roles}` - Number of roles
    `{emojis}` - Number of emojis
    `{boosts}` - Number of boosts
    `{boosters}` - Number of boosters
    `{owner}` - Server owner
    `{@owner}` - Server owner mention
    `{rules}` - Server rules channel
    `{afk}` - Server AFK channel
    `{system}` - Server system channel
    `{forums}` - Number of forum channels
    `{categories}` - Number of categories
    `{stage}` - Number of stage channels
    `{voice}` - Number of voice channels"""

        embed = discord.Embed(
            title="Mini-Tool Message Formatting",
            description=description,
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
async def get_channel(interaction: discord.Interaction, channel_ref: str = None):
    """Helper function to get a channel from a reference"""
    if not channel_ref:
        return interaction.channel
    
    try:
        # Try to get channel by ID
        channel_id = int(channel_ref)
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.followup.send("❌ Could not find the specified channel.", ephemeral=True)
            return None
        return channel
    except ValueError:
        await interaction.followup.send("❌ Invalid channel ID. Please provide a valid channel ID.", ephemeral=True)
        return None

async def extract_message_id(interaction: discord.Interaction, message_ref: str):
    """Helper function to extract a message from a reference (ID or URL)"""
    try:
        # Try to extract message ID from URL
        if '/' in message_ref:
            message_id = int(message_ref.split('/')[-1])
        else:
            message_id = int(message_ref)
            
        # Try to get the message
        message = await interaction.channel.fetch_message(message_id)
        return message
    except (ValueError, IndexError):
        await interaction.followup.send("❌ Invalid message reference. Please provide a valid message ID or URL.", ephemeral=True)
        return None
    except discord.NotFound:
        await interaction.followup.send("❌ Message not found. Make sure the message exists and is in this channel.", ephemeral=True)
        return None
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to access that message.", ephemeral=True)
        return None
    except Exception as e:
        await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
        return None

async def setup(bot):
    await bot.add_cog(Speaker(bot))