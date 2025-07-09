import discord; from discord import app_commands; from discord.ext import commands
import sqlite3, io; from typing import Optional

class Garbage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._create_tables()
    
    def _create_tables(self):
        """Create the bans table if it doesn't exist."""
        cursor = self.db.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS bans (
            guild_id INTEGER PRIMARY KEY,
            guild_name TEXT,
            reason TEXT,
            moderator_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        self.db.commit()
    
    def is_admin():
        """Check if the user is an admin or the bot owner."""
        async def predicate(ctx):
            # Check if user is bot owner
            if await ctx.bot.is_owner(ctx.author):
                return True
                
            # Check if user has admin role in the database
            c = ctx.bot.db.cursor()
            c.execute('SELECT role FROM users WHERE user_id = ?', (ctx.author.id,))
            result = c.fetchone()
            
            # Return True if user has 'admin' role
            return result is not None and result[0].lower() == 'admin'
        return commands.check(predicate)
    
    @commands.command(name="ban_guild", description="Ban a guild from using the bot")
    @is_admin()
    async def ban_guild(self, ctx, guild_id: int, *, reason: str = "No reason provided"):
        """Ban a guild from using the bot."""
        try:
            # Check if guild is already banned
            c = self.db.cursor()
            c.execute('SELECT * FROM bans WHERE guild_id = ?', (guild_id,))
            if c.fetchone():
                await ctx.send(f"❌ Guild `{guild_id}` is already banned.", ephemeral=True)
                return
            
            # Get guild name if possible
            guild = self.bot.get_guild(guild_id)
            guild_name = guild.name if guild else "Unknown Guild"
            
            # Add guild to ban list
            c.execute('''
                INSERT INTO bans (guild_id, guild_name, reason, moderator_id)
                VALUES (?, ?, ?, ?)
            ''', (guild_id, guild_name, reason, ctx.author.id))
            self.db.commit()
            
            # Make the bot leave the guild if it's currently in it
            guild = self.bot.get_guild(guild_id)
            if guild:
                try:
                    await guild.leave()
                    print(f"Left banned guild: {guild.name} ({guild_id})")
                except Exception as e:
                    print(f"Error leaving guild {guild_id}: {e}")
            
            await ctx.send(f"✅ Successfully banned guild `{guild_id}` (Reason: {reason}) and left the server.", ephemeral=True)
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
    
    @commands.command(name="unban_guild", description="Unban a guild from using the bot")
    @is_admin()
    async def unban_guild(self, ctx, guild_id: int):
        """Unban a guild from using the bot."""
        try:
            # Check if guild is actually banned
            c = self.db.cursor()
            c.execute('SELECT * FROM bans WHERE guild_id = ?', (guild_id,))
            if not c.fetchone():
                await ctx.send(f"❌ Guild `{guild_id}` is not currently banned.", ephemeral=True)
                return
            
            # Remove guild from ban list
            c.execute('DELETE FROM bans WHERE guild_id = ?', (guild_id,))
            self.db.commit()
            
            # Try to notify the guild if possible
            try:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    # Try to find a channel to send the message to
                    channel = guild.system_channel or next((c for c in guild.text_channels if c.permissions_for(guild.me).send_messages), None)
                    if channel:
                        embed = discord.Embed(
                            title="✅ Bot Access Restored",
                            description=f"This server has been unbanned and can now use {self.bot.user.mention} again.",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Moderator", value=f"<@{ctx.author.id}>", inline=True)
                        await channel.send(embed=embed)
            except Exception as e:
                pass
            
            await ctx.send(f"✅ Successfully unbanned guild `{guild_id}`.", ephemeral=True)
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
    
    @commands.command(name="ban_list", description="List all banned guilds in a text file")
    @is_admin()
    async def ban_list(self, ctx):
        """List all guilds that are banned from using the bot in a text file."""
        try:
            c = self.db.cursor()
            c.execute('''
                SELECT guild_id, guild_name, reason, moderator_id, timestamp 
                FROM bans 
                ORDER BY timestamp DESC
            ''')
            bans = c.fetchall()
            
            if not bans:
                await ctx.send("ℹ️ No guilds are currently banned.", ephemeral=True)
                return
            
            # Create a text file in memory
            output = "Banned Guilds\n" + "="*50 + "\n\n"
            
            for ban in bans:
                guild_id, guild_name, reason, mod_id, timestamp = ban
                
                output += f"Guild: {guild_name} (ID: {guild_id})\n"
                output += f"Reason: {reason}\n"
                output += f"Banned by: {mod_id}\n"
                output += f"Date: {timestamp}\n"
                output += "-"*50 + "\n"
            
            # Create file-like object in memory
            file = discord.File(
                fp=io.BytesIO(output.encode('utf-8')),
                filename='banned_guilds.txt'
            )
            
            await ctx.send("Here's the list of banned guilds:", file=file, ephemeral=True)
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred: {str(e)}", ephemeral=True)
    
    @commands.command(name="leave_guild", description="Make the bot leave a specified guild")
    @is_admin()
    async def leave_guild(self, ctx, guild_id: int):
        """
        Make the bot leave a specified guild by ID.
        
        Parameters
        -----------
        guild_id: int
            The ID of the guild to leave
        """
        try:
            # Find the guild
            guild = self.bot.get_guild(guild_id)
            if not guild:
                return await ctx.send(f"❌ I'm not in a guild with ID: {guild_id}")
            
            # Get guild info before leaving
            guild_name = guild.name
            member_count = guild.member_count
            
            # Leave the guild
            await guild.leave()
            
            # Send confirmation
            await ctx.send(
                f"✅ Left guild: **{guild_name}**\n"
                f"📋 Guild ID: `{guild_id}`\n"
                f"👥 Members: `{member_count}`",
                ephemeral=True
            )
            
            # Log the action
            print(f"Left guild: {guild_name} (ID: {guild_id}) with {member_count} members")
            
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to leave that guild.", ephemeral=True)
        except discord.HTTPException as e:
            await ctx.send(f"❌ An error occurred while trying to leave the guild: {e}", ephemeral=True)
        except Exception as e:
            await ctx.send(f"❌ An unexpected error occurred: {str(e)}", ephemeral=True)
    
    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Check if a guild is banned when the bot joins it."""
        try:
            c = self.db.cursor()
            c.execute('SELECT * FROM bans WHERE guild_id = ?', (guild.id,))
            if c.fetchone():
                # Leave the guild immediately if it's banned
                await guild.leave()
                print(f"Left banned guild: {guild.name} ({guild.id})")
        except Exception as e:
            print(f"Error checking guild ban status: {e}")
    
    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Clean up guild data when the bot is removed from a guild."""
        try:
            # Import the clean_guild_data function from database_utils
            from utils.database_utils import clean_guild_data
            
            # Clean up the guild's data
            result = await clean_guild_data(self.db, guild.id)
            
            if result['success']:
                pass
            else:
                print(f"Failed to clean up data for guild {guild.name} ({guild.id}): {result['error']}")
                
        except Exception as e:
            print(f"Error in on_guild_remove for guild {guild.id}: {e}")

async def setup(bot):
    await bot.add_cog(Garbage(bot))