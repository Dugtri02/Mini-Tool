# Import and Export ban lists

import discord
import asyncio
import time
import io
import re
from typing import List, Optional, Dict, Any, Tuple
from discord import app_commands, ui
from discord.ext import commands

class BanImportView(ui.View):
    def __init__(self, importer):
        super().__init__(timeout=None)
        self.importer = importer
        self.stop_requested = False
    
    @ui.button(label="⏹️ Stop Import", style=discord.ButtonStyle.danger, custom_id="stop_import")
    async def stop_import(self, interaction: discord.Interaction, button: ui.Button):
        self.stop_requested = True
        button.disabled = True
        button.label = "🛑 Stopping..."
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🛑 Stop requested. The import will complete the current batch and then stop.", ephemeral=True)


class ImpExp(commands.GroupCog, name="bans"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.import_queue = asyncio.Queue()
        self.export_queue = asyncio.Queue()
        self.processing_import = False
        self.processing_export = False
        self.active_imports: Dict[int, Dict[str, Any]] = {}  # interaction_id: {view, task}
        
    async def process_import_queue(self):
        if self.processing_import:
            return
            
        self.processing_import = True
        try:
            while not self.import_queue.empty():
                interaction, attachment = await self.import_queue.get()
                await self._process_import(interaction, attachment)
                self.import_queue.task_done()
        finally:
            self.processing_import = False
    
    async def process_export_queue(self, interaction: discord.Interaction, ephemeral: bool = True):
        if self.processing_export:
            await interaction.followup.send("Another export is in progress. Your request has been queued.", ephemeral=True)
            return
            
        self.processing_export = True
        try:
            await self._process_export(interaction, ephemeral)
        finally:
            self.processing_export = False
    
    async def _process_import(self, interaction: discord.Interaction, attachment: discord.Attachment):
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Read the attachment content
            content = await attachment.read()
            content = content.decode('utf-8')
            
            # Parse user IDs from the content
            user_ids = []
            for line in content.split('\n'):
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('//'):
                    continue
                    
                # Try to extract user ID from different formats
                if 'ID:' in line:
                    # Format: username (ID: 123456789012345678)
                    try:
                        user_id = int(line.split('ID:')[-1].strip(' )'))
                        user_ids.append(user_id)
                    except (ValueError, IndexError):
                        pass
                else:
                    # Try to parse as raw ID
                    try:
                        user_id = int(line.strip())
                        user_ids.append(user_id)
                    except ValueError:
                        pass
            
            if not user_ids:
                await interaction.followup.send("No valid user IDs found in the file.", ephemeral=True)
                return
                
            # Process bans
            success_count = 0
            failed_count = 0
            
            for user_id in user_ids:
                try:
                    user = await self.bot.fetch_user(user_id)
                    await interaction.guild.ban(user, reason=f"Banned via import by {interaction.user}")
                    success_count += 1
                except Exception as e:
                    failed_count += 1
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.5)
            
            await interaction.followup.send(
                f"Import complete!\n"
                f"• Successfully banned: {success_count}\n"
                f"• Failed to ban: {failed_count}",
                ephemeral=True
            )
            
        except Exception as e:
            await interaction.followup.send(f"An error occurred during import: {str(e)}", ephemeral=True)
    
    async def _process_export(self, interaction: discord.Interaction, ephemeral: bool = True):
        try:
            await interaction.response.defer(ephemeral=ephemeral)
            
            # Get banned members
            banned_members = []
            async for ban in interaction.guild.bans():
                banned_members.append(ban)
            
            if not banned_members:
                await interaction.followup.send("No banned members found in this server.", ephemeral=ephemeral)
                return
            
            # Create export content
            export_content = [
                f"# Banned Members Export - {interaction.guild.name} | {interaction.guild.id}",
                f"# Generated by: @{interaction.user} | {interaction.user.id}",
                f"# Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                f"# Total banned members: {len(banned_members)}",
                ""
            ]
            
            for ban in banned_members:
                export_content.append(f"{ban.user} (ID: {ban.user.id}) - Reason: {ban.reason or 'No reason provided'}")
            
            # Create and send the file
            file_content = '\n'.join(export_content)
            file = discord.File(
                io.BytesIO(file_content.encode('utf-8')),
                filename=f"{interaction.guild.name}_bans_{int(time.time())}.txt"
            )
            
            await interaction.followup.send(
                f"Exported {len(banned_members)} banned members:",
                file=file,
                ephemeral=ephemeral
            )
            
        except Exception as e:
            await interaction.followup.send(f"An error occurred during export: {str(e)}", ephemeral=ephemeral)
    
    @app_commands.command(name="export", description="Export banned members from this server")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(ephemeral="Set to False to show the export to everyone")
    async def export_bans(self, interaction: discord.Interaction, ephemeral: bool = True):
        """Export banned members from this server to a text file"""
        await self.process_export_queue(interaction, ephemeral)
    
    def _parse_user_ids(self, content: str) -> List[int]:
        """Parse user IDs from the format (ID: 123456789012345678)"""
        user_ids = []
        
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith(('#', '//', 'Generated by:', 'Date:', 'Total', '===')):
                continue
            
            # Only match the exact format: (ID: 123456789012345678)
            matches = re.findall(r'\(ID:\s*(\d+)\)', line)
            for match in matches:
                try:
                    user_id = int(match)
                    if 17 <= len(str(user_id)) <= 20:  # Discord IDs are 17-20 digits
                        user_ids.append(user_id)
                except (ValueError, IndexError):
                    continue
        
        return user_ids
    
    async def _process_import(self, interaction: discord.Interaction, attachment: discord.Attachment):
        try:
            # Read the attachment content
            content = await attachment.read()
            content = content.decode('utf-8')
            
            # Parse user IDs from the content
            user_ids = self._parse_user_ids(content)
            
            if not user_ids:
                await interaction.followup.send("No valid user IDs found in the file.", ephemeral=True)
                return
                
            # Create view with stop button
            view = BanImportView(self)
            
            # Send initial status message
            status_msg = await interaction.followup.send(
                f"⏳ Starting to process {len(user_ids)} bans...",
                view=view,
                ephemeral=True
            )
            
            # Store the active import
            self.active_imports[interaction.id] = {
                'view': view,
                'status_msg': status_msg,
                'total': len(user_ids),
                'success': 0,
                'failed': 0,
                'failed_users': []
            }
            
            # Process bans with rate limiting
            for i, user_id in enumerate(user_ids):
                if view.stop_requested:
                    break
                    
                try:
                    user = await self.bot.fetch_user(user_id)
                    await interaction.guild.ban(user, reason=f"Banned via import by {interaction.user}")
                    self.active_imports[interaction.id]['success'] += 1
                    print(f"✅ Banned user {user} (ID: {user_id}) in guild {interaction.guild.name} (ID: {interaction.guild.id})")
                except discord.NotFound:
                    self.active_imports[interaction.id]['failed_users'].append((user_id, "User not found"))
                    self.active_imports[interaction.id]['failed'] += 1
                except discord.Forbidden:
                    self.active_imports[interaction.id]['failed_users'].append((user_id, "Missing permissions"))
                    self.active_imports[interaction.id]['failed'] += 1
                except Exception as e:
                    self.active_imports[interaction.id]['failed_users'].append((user_id, str(e)))
                    self.active_imports[interaction.id]['failed'] += 1
                
                # Update progress every 5 bans or on last item
                if (i + 1) % 5 == 0 or i == len(user_ids) - 1:
                    progress = (i + 1) / len(user_ids) * 100
                    await self._update_import_progress(interaction.id, progress)
                
                # Small delay to avoid rate limits
                await asyncio.sleep(1.2)
            
            # Final update and cleanup
            await self._finalize_import(interaction.id, view.stop_requested)
            
        except Exception as e:
            await interaction.followup.send(f"An error occurred during import: {str(e)}", ephemeral=True)
        finally:
            # Clean up
            if interaction.id in self.active_imports:
                del self.active_imports[interaction.id]
    
    async def _update_import_progress(self, interaction_id: int, progress: float):
        if interaction_id not in self.active_imports:
            return
            
        data = self.active_imports[interaction_id]
        view = data['view']
        
        # Update the status message
        await data['status_msg'].edit(
            content=(
                f"⏳ Processing bans... {progress:.1f}%\n"
                f"✅ Success: {data['success']} | ❌ Failed: {data['failed']} | 📊 Total: {data['total']}"
            ),
            view=view
        )
    
    async def _finalize_import(self, interaction_id: int, was_stopped: bool):
        if interaction_id not in self.active_imports:
            return
            
        data = self.active_imports[interaction_id]
        view = data['view']
        
        # Disable the stop button
        for item in view.children:
            if item.custom_id == "stop_import":
                item.disabled = True
                item.label = "✅ Complete" if not was_stopped else "⏹️ Stopped"
        
        # Create results message
        result_message = [
            f"✅ Import {'completed' if not was_stopped else 'stopped'}!" if not was_stopped else "⏹️ Import stopped by user",
            f"• Total: {data['total']}",
            f"• Successfully banned: {data['success']}",
            f"• Failed: {data['failed']}"
        ]
        
        # Add failed users if any
        if data['failed_users']:
            failed_list = "\n".join([f"- {uid}: {reason}" for uid, reason in data['failed_users'][:10]])
            if len(data['failed_users']) > 10:
                failed_list += f"\n...and {len(data['failed_users']) - 10} more"
            result_message.append("\nFailed bans:" + failed_list)
        
        # Update the final message
        await data['status_msg'].edit(
            content="\n".join(result_message),
            view=view
        )
    
    @app_commands.command(name="import", description="Import banned members from a text file")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(attachment=".txt file containing user IDs to ban (format: ID: 123456789012345678)")
    async def import_bans(self, interaction: discord.Interaction, attachment: discord.Attachment):
        """Import banned members from a text file containing user IDs"""
        # Check file extension
        if not attachment.filename.lower().endswith('.txt'):
            await interaction.response.send_message(
                "❌ Error: Only .txt files are accepted for import.",
                ephemeral=True
            )
            return
            
        await interaction.response.defer(ephemeral=True)
        await self._process_import(interaction, attachment)

async def setup(bot: commands.Bot):
    await bot.add_cog(ImpExp(bot))