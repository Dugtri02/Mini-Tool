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
    
    def __init__(self, repo_owner: str, repo_name: str, branch: str = 'cog_handler'):
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
        """Download a file from GitHub and save it locally."""
        url = f"{self.raw_base_url}/{path}"
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    content = await response.text()
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    save_path.write_text(content, encoding='utf-8')
                    return True
                return False
        except Exception as e:
            logging.error(f"Error downloading file {path}: {e}")
            return False


class CogHandler(commands.Cog):    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger('cog_handler')
        self.cogs_dir = Path('cogs')
        self.loaded_cogs = set()
        self.gh_manager = GitHubCogManager("Dugtri02", "Mini-Tool")

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

    async def load_cogs(self, delay: float = 0.5):
        """
        Load all cogs found in the cogs directory and its subdirectories.
        
        Args:
            delay: Delay in seconds between loading each cog (default: 0.5s)
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
            
            # Skip if already loaded
            if module_path in self.loaded_cogs:
                self.logger.debug(f'Skipping already loaded cog: {module_path}')
                skipped += 1
                continue
            
            try:
                self.logger.info(f'Loading cog {idx}/{len(cog_files)}: {module_path}')
                await self.bot.load_extension(module_path)
                self.loaded_cogs.add(module_path)
                self.logger.info(f'Successfully loaded cog: {module_path}')
                loaded += 1
                
            except commands.ExtensionAlreadyLoaded:
                self.logger.debug(f'Cog already loaded: {module_path}')
                skipped += 1
                
            except Exception as e:
                self.logger.error(f'Failed to load cog {module_path}: {str(e)}', exc_info=True)
                failed += 1

        self.logger.info(f'Cog loading complete. Loaded: {loaded}, Skipped: {skipped}, Failed: {failed}')
        return {
            'loaded': loaded,
            'skipped': skipped,
            'failed': failed,
            'total': loaded + skipped + failed
        }

    @commands.hybrid_command(name="managecogs", with_app_command=True)
    @commands.is_owner()
    async def manage_cogs(self, ctx: commands.Context):
        """Manage cogs from GitHub repository (Owner only)."""
        await self.show_main_menu(ctx)

    async def show_main_menu(self, ctx: Union[commands.Context, discord.Interaction], message: discord.Message = None):
        """Show the main menu with available packages."""
        view = discord.ui.View(timeout=300)
        
        # Fetch packages from GitHub
        packages = await self.gh_manager.get_subfolders('cogs')
        
        if not packages:
            embed = discord.Embed(
                title="Cog Manager",
                description="No packages found in the repository.",
                color=discord.Color.red()
            )
            if message:
                await message.edit(embed=embed, view=None)
            else:
                await ctx.send(embed=embed)
            return
        
        # Create package selection dropdown
        select = discord.ui.Select(
            placeholder="Select a package",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label=pkg['name'], value=pkg['path'])
                for pkg in packages
            ]
        )
        
        async def select_callback(interaction: discord.Interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            selected_path = interaction.data['values'][0]
            await self.show_package_cogs(interaction, selected_path)
        
        select.callback = select_callback
        view.add_item(select)
        
        embed = discord.Embed(
            title="Cog Manager",
            description="Select a package to manage its cogs:",
            color=discord.Color.blue()
        )
        
        if message:
            await message.edit(embed=embed, view=view)
        else:
            await ctx.send(embed=embed, view=view)
    
    async def show_package_cogs(self, interaction: discord.Interaction, package_path: str):
        """Show cogs available in a package."""
        view = discord.ui.View(timeout=300)
        
        # Fetch cogs from the selected package
        cogs = await self.gh_manager.get_cog_files(package_path)
        
        if not cogs:
            embed = discord.Embed(
                title=f"Package: {package_path}",
                description="No cogs found in this package.",
                color=discord.Color.red()
            )
            await interaction.response.edit_message(embed=embed, view=None)
            return
        
        # Create cog selection dropdown
        select = discord.ui.Select(
            placeholder="Select cogs to download/update",
            min_values=1,
            max_values=len(cogs),
            options=[
                discord.SelectOption(label=cog['name'], value=cog['path'])
                for cog in cogs
            ]
        )
        
        async def download_callback(interaction: discord.Interaction):
            if interaction.user.id != interaction.message.interaction.user.id:
                await interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
                
            selected_paths = interaction.data['values']
            await self.download_cogs(interaction, selected_paths)
        
        async def back_callback(interaction: discord.Interaction):
            if interaction.user.id != interaction.message.interaction.user.id:
                await interaction.response.send_message("This is not your menu!", ephemeral=True)
                return
            
            ctx = await self.bot.get_context(interaction.message)
            await self.show_main_menu(ctx, interaction.message)
        
        select.callback = download_callback
        back_btn = discord.ui.Button(label="← Back", style=discord.ButtonStyle.secondary)
        back_btn.callback = back_callback
        
        view.add_item(select)
        view.add_item(back_btn)
        
        embed = discord.Embed(
            title=f"Package: {package_path}",
            description="Select cogs to download/update:",
            color=discord.Color.blue()
        )
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def download_cogs(self, interaction: discord.Interaction, cog_paths: List[str]):
        """Download selected cogs from GitHub."""
        view = discord.ui.View(timeout=None)
        
        embed = discord.Embed(
            title="Downloading Cogs",
            description="Please wait while the selected cogs are being downloaded...",
            color=discord.Color.blue()
        )
        
        message = await interaction.followup.send(embed=embed, wait=True)
        
        results = []
        for path in cog_paths:
            cog_name = Path(path).name
            save_path = Path("cogs") / path
            success = await self.gh_manager.download_file(path, save_path)
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
        
        if isinstance(interaction, discord.Interaction):
            await interaction.response.edit_message(embed=embed, view=None)
            message = interaction.message
        else:
            message = await interaction.channel.send(embed=embed)
        
        status = await self.load_cogs()
        
        embed = discord.Embed(
            title='Cog Reload Status',
            color=discord.Color.green() if status['failed'] == 0 else discord.Color.orange()
        )
        
        embed.add_field(name='✅ Loaded', value=str(status['loaded']))
        embed.add_field(name='⏩ Skipped', value=str(status['skipped']))
        embed.add_field(name='❌ Failed', value=str(status['failed']))
        
        await message.edit(embed=embed, view=None)
    
    @commands.hybrid_command(name="reloadcogs", with_app_command=True)
    @commands.is_owner()
    async def reload_cogs_command(self, ctx: commands.Context):
        """Reload all cogs (Owner only)."""
        await self.reload_cogs(ctx)

    def cog_unload(self):
        """Clean up resources when the cog is unloaded."""
        asyncio.create_task(self.gh_manager.close())

async def setup(bot: commands.Bot):
    """Set up the cog handler."""
    cog = CogHandler(bot)
    await bot.add_cog(cog)
    await cog.load_cogs()  # Load all cogs when the cog is loaded