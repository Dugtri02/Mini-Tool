import os
import importlib
import logging
import asyncio
import aiohttp
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
from urllib.parse import urljoin

import discord
from discord import app_commands
from discord.ext import commands

class GitHubCogManager:
    """Helper class to interact with GitHub API for cog management."""
    
    def __init__(self, repo_owner: str, repo_name: str, branch: str = 'main'):
        self.base_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
        self.raw_base_url = f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{branch}"
        self.branch = branch
        self.session = aiohttp.ClientSession()
        
    async def close(self):
        await self.session.close()
        
    async def get_subfolders(self, path: str = '') -> List[Dict[str, str]]:
        """Get all subfolders in a given GitHub repository path."""
        url = f"{self.base_url}/contents/{path}"
        params = {'ref': self.branch}
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    items = await response.json()
                    return [
                        {"name": item['name'], "path": item['path']}
                        for item in items 
                        if item['type'] == 'dir' and not item['name'].startswith('.')
                    ]
                return []
        except Exception as e:
            logging.error(f"Error fetching subfolders: {e}")
            return []
            
    async def get_cog_files(self, path: str) -> List[Dict[str, str]]:
        """Get all Python files in a given GitHub repository path."""
        url = f"{self.base_url}/contents/{path}"
        params = {'ref': self.branch}
        
        try:
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    items = await response.json()
                    return [
                        {"name": item['name'], "path": item['path']}
                        for item in items 
                        if item['type'] == 'file' and item['name'].endswith('.py')
                    ]
                return []
        except Exception as e:
            logging.error(f"Error fetching cog files: {e}")
            return []
            
    async def download_file(self, path: str, save_path: Path) -> bool:
        """
        Download a file from GitHub and save it locally.
        
        Args:
            path: The path to the file in the GitHub repository (e.g., 'cogs/molecord/compass.py')
            save_path: The local path where the file should be saved
            
        Returns:
            bool: True if download and save were successful, False otherwise
        """
        # Remove the 'cogs/' prefix from the path if it exists
        if path.startswith('cogs/'):
            relative_path = path[5:]  # Remove 'cogs/'
        else:
            relative_path = path
            
        # Create the full URL to download from
        url = f"{self.raw_base_url}/{path}"
        
        # Create the full local save path, ensuring we don't create an extra 'cogs' directory
        if str(save_path).startswith('cogs/') or str(save_path).startswith('cogs\\\\'):
            # If save_path already starts with cogs, use it as is
            final_save_path = Path(save_path)
        else:
            # Otherwise, create the path relative to the cogs directory
            final_save_path = Path('cogs') / relative_path
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    # Read the raw bytes to preserve original line endings
                    content = await response.read()
                    # Ensure the parent directory exists
                    final_save_path.parent.mkdir(parents=True, exist_ok=True)
                    # Save the file in binary mode to preserve original format
                    with open(final_save_path, 'wb') as f:
                        f.write(content)
                    logging.info(f"Successfully downloaded {path} to {final_save_path}")
                    return True
                logging.error(f"Failed to download {path}: HTTP {response.status}")
                return False
        except Exception as e:
            logging.error(f"Error downloading file {path}: {e}", exc_info=True)
            return False


class CogHandler(commands.Cog):    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('cog_handler')
        self.cogs_dir = Path('cogs')
        self.loaded_cogs = set()
        # Default repository settings
        self.repo_owner = "Dugtri02"
        self.repo_name = "Mini-Tool"
        self.branch = "main"
        self.gh_manager = GitHubCogManager(self.repo_owner, self.repo_name, self.branch)
        
    def update_github_manager(self, repo_owner: str = None, repo_name: str = None, branch: str = None):
        """Update the GitHub manager with new repository details."""
        if repo_owner:
            self.repo_owner = repo_owner
        if repo_name:
            self.repo_name = repo_name
        if branch:
            self.branch = branch
            
        # Close the old session
        asyncio.create_task(self.gh_manager.close())
        
        # Create a new manager with updated settings
        self.gh_manager = GitHubCogManager(
            self.repo_owner,
            self.repo_name,
            self.branch
        )
        return self.gh_manager

    @commands.Cog.listener()
    async def on_ready(self):
        self.logger.info('Cog handler is ready')

    def _find_cog_files(self) -> list[tuple[Path, str]]:
        """Recursively find all Python files in the cogs directory that could be cogs."""
        cog_files = []
        for path in self.cogs_dir.rglob('*.py'):
            # Skip __init__.py, files starting with _, and the cog handler itself
            if (path.stem.startswith('_') or 
                path.name == '__init__.py' or 
                path.name == 'cog_handler.py'):
                continue
            
            # Convert path to module path (e.g., cogs/folder/module -> cogs.folder.module)
            rel_path = path.relative_to(self.cogs_dir.parent)
            module_path = str(rel_path.with_suffix('')).replace(os.sep, '.')
            
            cog_files.append((path, module_path))
        
        return cog_files

    async def load_cogs(self, delay: float = 1.2, reload_existing: bool = False):
        """
        Load all cogs found in the cogs directory and its subdirectories.
        
        Args:
            delay: Delay in seconds between loading each cog (default: 0.5s)
            reload_existing: Whether to reload already loaded cogs (default: False)
        """
        self.logger.info(f'Starting to load cogs with {delay}s delay between loads...')
        
        cog_files = self._find_cog_files()
        if not cog_files:
            self.logger.warning('No cog files found!')
            return

        loaded = 0
        skipped = 0
        failed = 0

        for idx, (path, module_path) in enumerate(cog_files, 1):
            # Add delay between cog loads (except for the first one)
            if idx > 1:
                self.logger.debug(f'Waiting {delay} seconds before loading next cog...')
                await asyncio.sleep(delay)
            
            # Check if cog is already loaded
            is_loaded = module_path in self.loaded_cogs or module_path in self.bot.extensions
            
            if is_loaded and not reload_existing:
                self.logger.debug(f'Skipping already loaded cog: {module_path}')
                skipped += 1
                continue
            
            try:
                self.logger.info(f'Loading cog {idx}/{len(cog_files)}: {module_path}')
                
                # Unload first if reloading
                if is_loaded and reload_existing:
                    await self.bot.unload_extension(module_path)
                    if module_path in self.loaded_cogs:
                        self.loaded_cogs.remove(module_path)
                
                # Load the cog
                await self.bot.load_extension(module_path)
                self.loaded_cogs.add(module_path)
                
                action = 'Reloaded' if is_loaded else 'Loaded'
                self.logger.info(f'{action} cog: {module_path}')
                loaded += 1
                
            except commands.ExtensionAlreadyLoaded:
                self.logger.debug(f'Cog already loaded (race condition?): {module_path}')
                skipped += 1
                
            except Exception as e:
                self.logger.error(f'Failed to load cog {module_path}: {str(e)}', exc_info=True)
                failed += 1

        self.logger.info(f'Cog loading complete. Loaded: {loaded}, Skipped: {skipped}, Failed: {failed}')
        
        # Sync the command tree to update slash commands
        try:
            await self.bot.tree.sync()
            self.logger.info('Successfully synced application commands')
        except Exception as e:
            self.logger.error(f'Failed to sync application commands: {str(e)}', exc_info=True)
            
        return {
            'loaded': loaded,
            'skipped': skipped,
            'failed': failed,
            'total': loaded + skipped + failed
        }

    @app_commands.command(name="packages", description="Manage cogs from a GitHub repository (Owner only)")
    @app_commands.describe(
        repo_owner="Repository owner (default: Dugtri02)",
        repo_name="Repository name (default: Mini-Tool)",
        branch="Branch name (default: main)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def manage_cogs(
        self,
        interaction: discord.Interaction,
        repo_owner: str = None,
        repo_name: str = None,
        branch: str = None
    ):
        """
        Manage cogs from a GitHub repository (Owner only).
        
        Args:
            repo_owner: GitHub repository owner (username/organization)
            repo_name: GitHub repository name
            branch: Branch name (default: main)
        """
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("This command is only available to the bot owner.", ephemeral=True)
            return
            
        # Update repository settings if provided
        if repo_owner or repo_name or branch:
            try:
                self.update_github_manager(repo_owner, repo_name, branch)
                status_msg = f"✅ Using repository: {self.repo_owner}/{self.repo_name} (branch: {self.branch})"
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Failed to update repository settings: {e}",
                    ephemeral=True
                )
                return
        else:
            status_msg = f"ℹ️ Using default repository: {self.repo_owner}/{self.repo_name} (branch: {self.branch})"
        
        await interaction.response.defer(ephemeral=True)
        
        # Send initial status message
        if repo_owner or repo_name or branch:
            await interaction.followup.send(status_msg, ephemeral=True)
            
        await self.show_main_menu(interaction, None)

    async def show_main_menu(self, interaction: discord.Interaction, message: discord.Message = None):
        """Show the main menu with available packages."""
        view = discord.ui.View(timeout=300)
        
        # Add repo info button
        async def repo_info_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            embed = discord.Embed(
                title="Repository Information",
                description=(
                    f"**Repository:** {self.repo_owner}/{self.repo_name}\n"
                    f"**Branch:** {self.branch}\n\n"
                    "To change the repository, use the command with parameters:\n"
                    "`/managecogs repo_owner:username repo_name:repository branch:main`"
                ),
                color=discord.Color.blue()
            )
            await btn_interaction.response.send_message(embed=embed, ephemeral=True)
        
        repo_info_btn = discord.ui.Button(label="ℹ️ Repo Info", style=discord.ButtonStyle.secondary)
        repo_info_btn.callback = repo_info_callback
        view.add_item(repo_info_btn)
        
        # Add refresh button
        async def refresh_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            await btn_interaction.response.defer(ephemeral=True)
            await self.show_main_menu(interaction, None)
        
        refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.primary)
        refresh_btn.callback = refresh_callback
        view.add_item(refresh_btn)
        
        # Fetch packages from GitHub
        try:
            packages = await self.gh_manager.get_subfolders('cogs')
        except Exception as e:
            self.logger.error(f"Error fetching packages: {e}", exc_info=True)
            error_embed = discord.Embed(
                title="Error",
                description=(
                    f"Failed to fetch packages from {self.repo_owner}/{self.repo_name} (branch: {self.branch})\n"
                    f"Error: {str(e)}\n\n"
                    "Please check the repository details and try again."
                ),
                color=discord.Color.red()
            )
            # Create an empty view for error messages
            error_view = discord.ui.View(timeout=180)  # 3 minute timeout
            
            if message:
                await message.edit(embed=error_embed, view=error_view)
            else:
                await interaction.followup.send(embed=error_embed, view=error_view)
            return
        
        if not packages:
            embed = discord.Embed(
                title="Cog Manager",
                description="No packages found in the repository.",
                color=discord.Color.red()
            )
            # Create an empty view for the no packages message
            empty_view = discord.ui.View(timeout=180)
            if message:
                await message.edit(embed=embed, view=empty_view)
            else:
                await interaction.followup.send(embed=embed, view=empty_view, ephemeral=True)
            return
        
        # Create package selection dropdown
        select = discord.ui.Select(
            placeholder="Select a package to view cogs",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=pkg['name'], value=pkg['path'])
                for pkg in packages
            ]
        )
        
        async def select_callback(menu_interaction: discord.Interaction):
            if menu_interaction.user.id != interaction.user.id:
                await menu_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            selected_path = menu_interaction.data['values'][0]
            await menu_interaction.response.defer()
            await self.show_package_cogs(menu_interaction, selected_path)
        
        select.callback = select_callback
        view.add_item(select)
        
        embed = discord.Embed(
            title="Cog Manager",
            description="Select a package to view available cogs:",
            color=discord.Color.blue()
        )
        
        if message:
            await message.edit(embed=embed, view=view)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    def is_cog_loaded(self, cog_path: str) -> bool:
        """Check if a cog is currently loaded in the bot."""
        # Convert path to module path format (e.g., 'cogs/fun/example.py' -> 'cogs.fun.example')
        if cog_path.startswith('cogs/'):
            cog_path = cog_path[5:]  # Remove 'cogs/' prefix if present
        module_path = cog_path.replace('/', '.').replace('\\', '.').replace('.py', '')
        
        # Check if the module is in loaded_cogs or bot.extensions
        return (f'cogs.{module_path}' in self.loaded_cogs or 
                f'cogs.{module_path}' in self.bot.extensions)
    
    def is_cog_downloaded(self, cog_path: str) -> bool:
        """Check if a cog file exists in the local cogs directory."""
        # Convert path to local file path
        if not cog_path.startswith('cogs/'):
            cog_path = f'cogs/{cog_path}'
        local_path = Path(cog_path)
        return local_path.exists()

    async def show_package_cogs(self, interaction: discord.Interaction, package_path: str):
        """Show cogs available in a package."""
        view = discord.ui.View(timeout=300)
        
        # Add back to main menu button
        async def back_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            await btn_interaction.response.defer(ephemeral=True)
            await self.show_main_menu(interaction, None)
        
        back_btn = discord.ui.Button(label="⬅️ Back to Packages", style=discord.ButtonStyle.secondary)
        back_btn.callback = back_callback
        view.add_item(back_btn)
        
        # Add repo info button
        async def repo_info_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            embed = discord.Embed(
                title="Repository Information",
                description=(
                    f"**Repository:** {self.repo_owner}/{self.repo_name}\n"
                    f"**Branch:** {self.branch}\n"
                    f"**Current Package:** `{package_path}`\n\n"
                    "To change the repository, use the command with parameters:\n"
                    "`/managecogs repo_owner:username repo_name:repository branch:main`"
                ),
                color=discord.Color.blue()
            )
            await btn_interaction.response.send_message(embed=embed, ephemeral=True)
        
        repo_info_btn = discord.ui.Button(label="ℹ️ Repo Info", style=discord.ButtonStyle.secondary)
        repo_info_btn.callback = repo_info_callback
        view.add_item(repo_info_btn)
        
        # Add refresh button
        async def refresh_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            await btn_interaction.response.defer(ephemeral=True)
            await self.show_package_cogs(interaction, package_path)
        
        refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.primary)
        refresh_btn.callback = refresh_callback
        view.add_item(refresh_btn)
        
        # Fetch cogs from the selected package
        cogs = await self.gh_manager.get_cog_files(package_path)
        
        if not cogs:
            embed = discord.Embed(
                title=f"Package: {package_path}",
                description="No cogs found in this package.",
                color=discord.Color.red()
            )
            empty_view = discord.ui.View(timeout=180)
            if hasattr(interaction, 'response') and not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=empty_view, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, view=empty_view, ephemeral=True)
            return
        
        # Create cog selection dropdown with status indicators
        select = discord.ui.Select(
            placeholder="Select cogs to download/update",
            min_values=1,
            max_values=len(cogs),
            options=[]
        )
        
        # Add cogs to dropdown with status indicators
        for cog in cogs:
            is_loaded = self.is_cog_loaded(cog['path'])
            is_downloaded = self.is_cog_downloaded(cog['path'])
            
            # Create label with status indicators
            status = []
            if is_loaded:
                status.append("🟢")  # Green circle for loaded
            elif is_downloaded:
                status.append("🔵")  # Blue circle for downloaded but not loaded
            else:
                status.append("⚪")  # White circle for not downloaded
                
            label = f"{' '.join(status)} {cog['name']}"
            
            # Add description based on status
            description = None
            if is_loaded:
                description = "Loaded and ready to use"
            elif is_downloaded:
                description = "Downloaded but not loaded"
                
            select.add_option(
                label=label,
                value=cog['path'],
                description=description,
                emoji=None  # We're already adding emoji to the label
            )
        
        async def download_callback(menu_interaction: discord.Interaction):
            if menu_interaction.user.id != interaction.user.id:
                await menu_interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            selected_paths = menu_interaction.data['values']
            await menu_interaction.response.defer()
            await self.download_cogs(menu_interaction, selected_paths)
        
        # Set up the select menu callback
        select.callback = download_callback
        view.add_item(select)
        
        # Create the embed for the package view
        embed = discord.Embed(
            title=f"Package: {package_path}",
            description="Select cogs to download/update:",
            color=discord.Color.blue()
        )
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    
    async def download_cogs(self, interaction: discord.Interaction, cog_paths: List[str]):
        """Download selected cogs from GitHub."""
        view = discord.ui.View(timeout=None)
        
        embed = discord.Embed(
            title="Downloading Cogs",
            description="Please wait while the selected cogs are being downloaded...",
            color=discord.Color.blue()
        )
        
        # Send initial message
        message = await interaction.followup.send(embed=embed, wait=True, ephemeral=True)
        
        # Download cogs
        results = []
        for path in cog_paths:
            cog_name = Path(path).name
            # Pass the path directly - download_file will handle the 'cogs/' prefix
            success = await self.gh_manager.download_file(path, Path(path))
            results.append((cog_name, success))
        
        # Update with results
        success_list = [f"✅ {name}" for name, success in results if success]
        failed_list = [f"❌ {name}" for name, success in results if not success]
        
        description = []
        if success_list:
            description.append("**Successfully downloaded/updated:**\n" + "\n".join(success_list))
        if failed_list:
            description.append("\n**Failed to download/update:**\n" + "\n".join(failed_list))
        
        embed = discord.Embed(
            title="Download Complete",
            description="\n".join(description) if description else "No cogs were processed.",
            color=discord.Color.green() if success_list and not failed_list else 
                 discord.Color.red() if not success_list and failed_list else
                 discord.Color.orange()
        )
        
        # Add reload button
        async def reload_callback(btn_interaction: discord.Interaction):
            if btn_interaction.user.id != interaction.user.id:
                await btn_interaction.response.send_message("You didn't start this interaction!", ephemeral=True)
                return
                
            await btn_interaction.response.defer(ephemeral=True)
            await self.reload_cogs(btn_interaction)
        
        reload_btn = discord.ui.Button(label="🔄 Reload Cogs", style=discord.ButtonStyle.primary)
        reload_btn.callback = reload_callback
        view.add_item(reload_btn)
        
        await message.edit(embed=embed, view=view)
    
    async def reload_cogs(self, interaction: discord.Interaction):
        """Reload all cogs and show status."""
        embed = discord.Embed(
            title="Reloading Cogs",
            description="Please wait while cogs are being reloaded...",
            color=discord.Color.blue()
        )
        
        if interaction.response.is_done():
            message = await interaction.followup.send(embed=embed, wait=True, ephemeral=True)
        else:
            await interaction.response.defer(ephemeral=True)
            message = await interaction.original_response()
            await message.edit(embed=embed)
        
        # Force reload of all cogs, including those already loaded
        status = await self.load_cogs(reload_existing=True)
        
        embed = discord.Embed(
            title='Cog Reload Status',
            color=discord.Color.green() if status['failed'] == 0 else discord.Color.orange()
        )
        
        embed.add_field(name='✅ Loaded', value=str(status['loaded']))
        embed.add_field(name='⏩ Skipped', value=str(status['skipped']))
        embed.add_field(name='❌ Failed', value=str(status['failed']))
        
        await message.edit(embed=embed, view=None)
    
    @app_commands.command(name="reloadcogs", description="Reload all cogs (Owner only)")
    @app_commands.checks.has_permissions(administrator=True)
    async def reload_cogs_command(self, interaction: discord.Interaction):
        """Reload all cogs (Owner only)."""
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message("This command is only available to the bot owner.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await self.reload_cogs(interaction)

    def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        asyncio.create_task(self.gh_manager.close())

async def setup(bot: commands.Bot):
    """Set up the cog handler."""
    cog = CogHandler(bot)
    await bot.add_cog(cog)
    await cog.load_cogs()  # Load all cogs when the cog is loaded