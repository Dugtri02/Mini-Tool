# Automatically forward messages between channels/threads

import discord; from discord import app_commands; from discord.ext import commands
import sqlite3; from typing import Optional, List

class Envelope(commands.GroupCog, name="envelope"):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self._create_table()
    
    def _create_table(self):
        """Create the message_forwards table if it doesn't exist"""
        cursor = self.db.cursor()
        # New schema, just create the table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_forwards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                from_channel_id INTEGER NOT NULL,   
                to_channel_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                detect_type TEXT NOT NULL DEFAULT 'all',
                UNIQUE(guild_id, from_channel_id, to_channel_id, keyword, detect_type)
            )
        ''')
        self.db.commit()
    
    @app_commands.command(name="set", description="Set up message forwarding from one channel to another based on a keyword")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        from_channel_id="The ID of the channel/thread to listen for messages in (right-click → Copy ID)",
        to_channel_id="The ID of the channel/thread to forward matching messages to (right-click → Copy ID)",
        keyword="The keyword that triggers forwarding (case-insensitive)",
        detect_type="Which messages to forward: 'users', 'bots', or 'all' (default)"
    )
    @app_commands.choices(
        detect_type=[
            app_commands.Choice(name="users", value="users"),
            app_commands.Choice(name="bots", value="bots"),
            app_commands.Choice(name="all", value="all")
        ]
    )
    async def set_forward(self, interaction: discord.Interaction, 
                         from_channel_id: str,  # Changed to string to handle large IDs
                         to_channel_id: str,    # Changed to string to handle large IDs
                         keyword: str,
                         detect_type: Optional[str] = 'all'):
        """Set up message forwarding between channels/threads"""
        # Validate detect_type
        detect_type = detect_type.lower() if detect_type else 'all'
        if detect_type not in ['users', 'bots', 'all']:
            await interaction.response.send_message(
                "❌ Invalid detect_type. Must be one of: 'users', 'bots', or 'all'",
                ephemeral=True
            )
            return
            
        keyword = keyword.lower().strip()
        
        # Convert string IDs to integers
        try:
            from_id = int(from_channel_id)
            to_id = int(to_channel_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid channel ID format. Please provide valid numeric IDs.", ephemeral=True)
            return
            
        if from_id == to_id:
            await interaction.response.send_message("Source and destination channels/threads cannot be the same.", ephemeral=True)
            return
            
        # Verify the channels/threads exist and the bot can access them
        from_channel = interaction.guild.get_channel_or_thread(from_id)
        to_channel = interaction.guild.get_channel_or_thread(to_id)
        
        if not from_channel:
            await interaction.response.send_message("❌ Could not find the source channel/thread. Make sure the ID is correct and I have access to it.", ephemeral=True)
            return
            
        if not to_channel:
            await interaction.response.send_message("❌ Could not find the destination channel/thread. Make sure the ID is correct and I have access to it.", ephemeral=True)
            return
            
        try:
            with self.db:
                cursor = self.db.cursor()
                # Check if this exact rule already exists
                cursor.execute('''
                    SELECT id FROM message_forwards 
                    WHERE guild_id = ? AND from_channel_id = ? AND to_channel_id = ? 
                    AND keyword = ? AND detect_type = ?
                ''', (interaction.guild_id, from_id, to_id, keyword, detect_type))
                
                if cursor.fetchone() is not None:
                    await interaction.response.send_message(
                        f"ℹ️ A forwarding rule already exists from {from_channel.mention} to {to_channel.mention} "
                        f"for keyword: `{keyword}` with detect_type: `{detect_type}`",
                        ephemeral=True
                    )
                    return
                
                # Insert the new forwarding rule
                cursor.execute('''
                    INSERT INTO message_forwards 
                    (guild_id, from_channel_id, to_channel_id, keyword, detect_type)
                    VALUES (?, ?, ?, ?, ?)
                ''', (interaction.guild_id, from_id, to_id, keyword, detect_type))
            
                # Get the count of rules for this from_channel
                cursor.execute('''
                    SELECT COUNT(*) FROM message_forwards 
                    WHERE guild_id = ? AND from_channel_id = ?
                ''', (interaction.guild_id, from_id))
                rule_count = cursor.fetchone()[0]
            
            await interaction.response.send_message(
                f"✅ Set up forwarding from {from_channel.mention} to {to_channel.mention} "
                f"for keyword: `{keyword}` (detect_type: `{detect_type}`)\n"
                f"There are now {rule_count} forwarding rules for {from_channel.mention}",
                ephemeral=True
            )
        except sqlite3.Error as e:
            await interaction.response.send_message(
                f"❌ An error occurred while setting up the forward: {e}",
                ephemeral=True
            )
    
    
    
    async def from_channel_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for source channels that have forwarding rules"""
        
        cursor = self.db.cursor()
        cursor.execute('''
            SELECT DISTINCT from_channel_id 
            FROM message_forwards 
            WHERE guild_id = ?
            ORDER BY from_channel_id
        ''', (interaction.guild.id,))
        
        channels = []
        for row in cursor.fetchall():
            channel_id = row[0]
            channel = interaction.guild.get_channel_or_thread(channel_id)
            if channel:
                name = f"#{getattr(channel, 'name', 'unknown')}"
                # Make sure we're using string values for the choice
                channels.append(app_commands.Choice(name=name, value=str(channel_id)))
        
        return channels

    async def keyword_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for keywords based on the selected channel"""
        
        # Get the from_channel_id from the interaction options
        from_channel_id = None
        options = interaction.data.get('options', [])
        
        # Handle nested command structure (options are inside the 'remove' subcommand)
        if options and isinstance(options[0], dict) and 'options' in options[0]:
            options = options[0]['options']
        
        # Find the from_channel_id in the options
        for option in options:
            if option.get('name') == 'from_channel_id':
                from_channel_id = option.get('value')
                try:
                    # Convert to int if it's a string
                    from_channel_id = int(from_channel_id) if from_channel_id else None
                except (ValueError, TypeError) as e:
                    return []
                break
        
        if not from_channel_id:
            return []
            
        try:
            cursor = self.db.cursor()
            cursor.execute('''
                SELECT DISTINCT keyword 
                FROM message_forwards 
                WHERE guild_id = ? AND from_channel_id = ?
                ORDER BY keyword
            ''', (interaction.guild.id, from_channel_id))
            
            keywords = cursor.fetchall()
            
            return [
                app_commands.Choice(name=row[0], value=row[0])
                for row in keywords
            ]
        except Exception as e:
            print(f"Error in keyword_autocomplete: {e}")
            return []

    @app_commands.command(name="remove", description="Remove a message forwarding rule")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        from_channel_id="The source channel/thread to remove rules from",
        keyword="The keyword of the rule to remove"
    )
    @app_commands.autocomplete(
        from_channel_id=from_channel_autocomplete,
        keyword=keyword_autocomplete
    )
    async def remove_forward(self, interaction: discord.Interaction, 
                           from_channel_id: str,  # Changed to string to handle large IDs
                           keyword: str):
        """Remove a message forwarding rule"""
        # Convert string ID to integer
        try:
            channel_id = int(from_channel_id)
        except ValueError:
            await interaction.response.send_message("❌ Invalid channel ID format. Please provide a valid numeric ID.", ephemeral=True)
            return
            
        keyword = keyword.lower().strip()
        
        # Verify the channel/thread exists
        from_channel = interaction.guild.get_channel_or_thread(channel_id)
        channel_mention = f"<#{channel_id}>" if from_channel else f"channel/thread {channel_id}"
        
        with self.db:
            cursor = self.db.cursor()
            
            # First, get all matching rules to show in the confirmation
            cursor.execute('''
                SELECT id, to_channel_id, detect_type 
                FROM message_forwards 
                WHERE guild_id = ? AND from_channel_id = ? AND keyword = ?
            ''', (interaction.guild.id, channel_id, keyword))
            
            rules = cursor.fetchall()
            
            if not rules:
                await interaction.response.send_message(
                    f"❌ No forwarding rules found from {channel_mention} with keyword: `{keyword}`",
                    ephemeral=True
                )
                return
                
            # Delete all matching rules
            cursor.execute('''
                DELETE FROM message_forwards 
                WHERE guild_id = ? AND from_channel_id = ? AND keyword = ?
            ''', (interaction.guild.id, channel_id, keyword))
            
            # Format the rule details for the response
            rule_details = []
            for rule_id, to_channel_id, detect_type in rules:
                to_channel = interaction.guild.get_channel_or_thread(to_channel_id)
                to_mention = f"<#{to_channel_id}>" if to_channel else f"channel/thread {to_channel_id}"
                rule_details.append(f"- To: {to_mention} (detect_type: `{detect_type}`)")
            
            await interaction.response.send_message(
                f"✅ Removed {len(rules)} forwarding rule(s) from {channel_mention} for keyword: `{keyword}`\n"
                "\n".join(rule_details),
                ephemeral=True
            )
    
    @app_commands.command(name="list", description="List all message forwarding rules")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        page="Page number to view (5 rules per page)"
    )
    async def list_forwards(self, interaction: discord.Interaction, page: Optional[int] = 1):
        """List all message forwarding rules with pagination"""
        if page < 1:
            page = 1
        
        items_per_page = 5
        offset = (page - 1) * items_per_page
        
        cursor = self.db.cursor()
        
        # Get total count for pagination
        cursor.execute('''
            SELECT COUNT(*) FROM message_forwards 
            WHERE guild_id = ?
        ''', (interaction.guild.id,))
        total_rules = cursor.fetchone()[0]
        
        if total_rules == 0:
            await interaction.response.send_message(
                "No message forwarding rules have been set up yet.",
                ephemeral=True
            )
            return
        
        # Get paginated results with channel names for grouping
        cursor.execute('''
            SELECT 
                from_channel_id, 
                to_channel_id, 
                keyword, 
                detect_type,
                (SELECT name FROM sqlite_master WHERE type='table' AND name='message_forwards' AND sql LIKE '%id INTEGER PRIMARY KEY%') as has_id_column
            FROM message_forwards 
            WHERE guild_id = ?
            ORDER BY from_channel_id, keyword, to_channel_id
            LIMIT ? OFFSET ?
        ''', (interaction.guild.id, items_per_page, offset))
        
        rules = cursor.fetchall()
        
        if not rules and page > 1:
            await interaction.response.send_message(
                f"No rules found on page {page}. Try a lower page number.",
                ephemeral=True
            )
            return
        
        # Group rules by from_channel_id and keyword
        grouped_rules = {}
        for row in rules:
            from_channel_id, to_channel_id, keyword, detect_type, _ = row
            key = (from_channel_id, keyword)
            if key not in grouped_rules:
                grouped_rules[key] = []
            grouped_rules[key].append((to_channel_id, detect_type))
        
        # Build the embed
        total_pages = (total_rules + items_per_page - 1) // items_per_page  # Ceiling division
        embed = discord.Embed(
            title=f"Message Forwarding Rules (Page {page}/{total_pages})",
            description=f"Found {total_rules} total rules across {len(grouped_rules)} unique source/keyword combinations\n\n**Key:** `detect_type` can be: `users`, `bots`, or `all`",
            color=discord.Color.blue()
        )
        
        # Add each group of rules to the embed
        for (from_channel_id, keyword), destinations in grouped_rules.items():
            from_channel = interaction.guild.get_channel_or_thread(from_channel_id)
            from_mention = f"<#{from_channel_id}>" if from_channel else f"`{from_channel_id}` (Not Found)"
            
            # Format all destinations for this from_channel_id and keyword
            dest_lines = []
            for to_channel_id, detect_type in destinations:
                to_channel = interaction.guild.get_channel_or_thread(to_channel_id)
                to_mention = f"<#{to_channel_id}>" if to_channel else f"`{to_channel_id}` (Not Found)"
                dest_lines.append(f"- To: {to_mention} (Filter: `{detect_type}`)")
            
            # Add a field for this from_channel_id and keyword
            embed.add_field(
                name=f"From: {from_mention} | Keyword: `{keyword}`",
                value="\n".join(dest_lines),
                inline=False
            )
        
        # Add pagination controls
        if total_pages > 1:
            embed.set_footer(
                text=f"Page {page} of {total_pages} | "
                     f"Use /messenger list page:<number> to view more"
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def _check_message_content(self, message, keyword):
        """Check if message content or embeds contain the keyword"""
        # Check message content
        if keyword.lower() in message.content.lower():
            return True
            
        # Check embed contents
        for embed in message.embeds:
            # Check embed title
            if embed.title and keyword.lower() in embed.title.lower():
                return True
                
            # Check embed description
            if embed.description and keyword.lower() in embed.description.lower():
                return True
                
            # Check embed fields
            for field in getattr(embed, 'fields', []):
                if field.name and keyword.lower() in field.name.lower():
                    return True
                if field.value and keyword.lower() in field.value.lower():
                    return True
                    
            # Check embed footer
            if embed.footer and embed.footer.text and keyword.lower() in embed.footer.text.lower():
                return True
                
            # Check embed author
            if embed.author and embed.author.name and keyword.lower() in embed.author.name.lower():
                return True
                
        return False
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle message forwarding based on rules"""
        # Skip if not in a guild or if the message is from this bot
        if not message.guild or not message.channel or message.author == self.bot.user:
            return
            
        # Skip if message doesn't have required attributes
        if not hasattr(message, 'guild') or not hasattr(message, 'channel') or not hasattr(message, 'content'):
            return
            
        # Skip ephemeral messages (they won't have a guild)
        if message.is_system():
            return
            
        cursor = self.db.cursor()
        cursor.execute('''
            SELECT to_channel_id, keyword, detect_type 
            FROM message_forwards 
            WHERE guild_id = ? AND from_channel_id = ?
        ''', (message.guild.id, message.channel.id))
        
        rules = cursor.fetchall()
        
        for to_channel_id, keyword, detect_type in rules:
            # Skip if message doesn't match detect_type
            if detect_type == 'users' and message.author.bot:
                continue
            elif detect_type == 'bots' and not message.author.bot:
                continue
            
            if await self._check_message_content(message, keyword):
                to_channel = message.guild.get_channel_or_thread(to_channel_id)
                if to_channel:
                    try:
                        # Forward the message to the destination channel/thread
                        await message.forward(to_channel, fail_if_not_exists=False)
                    except Exception as e:
                        # Optionally send an error message to the channel
                        try:
                            await to_channel.send(f"❌ Failed to forward message: {e}")
                        except:
                            pass

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Envelope(bot)) 