import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from typing import Literal, Dict, Any

# A view for the stop button
class MassTagView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.stop_requested = False

    @discord.ui.button(label="⏹️ Stop", style=discord.ButtonStyle.danger, custom_id="stop_mass_tag")
    async def stop_task(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop_requested = True
        button.disabled = True
        button.label = "🛑 Stopping..."
        await interaction.response.edit_message(view=self)
        await interaction.followup.send("🛑 Stop requested. The task will stop after the current post.", ephemeral=True)

class MassTagger(commands.GroupCog, name="postman"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tag_queue = asyncio.Queue()
        self.active_tasks: Dict[int, Dict[str, Any]] = {}
        self.queue_processor.start()

    def cog_unload(self):
        self.queue_processor.cancel()

    @tasks.loop(seconds=2)
    async def queue_processor(self):
        if not self.tag_queue.empty():
            interaction, forum, tag_to_modify, action, filter_name, filter_tag, filter_no_tags, view, queued_message = await self.tag_queue.get()
            try:
                await self._process_tagging_task(interaction, forum, tag_to_modify, action, filter_name, filter_tag, filter_no_tags, view, queued_message)
            except Exception as e:
                print(f"Error processing tagging task: {e}")
            finally:
                self.tag_queue.task_done()

    async def tag_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        forum_id = interaction.namespace.forum.id if interaction.namespace.forum else None
        if not forum_id:
            return []

        forum_channel = self.bot.get_channel(forum_id)
        if not isinstance(forum_channel, discord.ForumChannel):
            return []

        return [
            app_commands.Choice(name=tag.name, value=tag.name)
            for tag in forum_channel.available_tags
            if current.lower() in tag.name.lower()
        ][:25]

    @app_commands.command(name="modify", description="Mass add|remove the tags on forum posts, includes filters.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        forum="The forum channel to modify.",
        tag="The name of the tag to add or remove.",
        action="Whether to add or remove the tag.",
        filter_name="Only process posts containing this text in the title.",
        filter_tag="Only process posts that already have this tag.",
        filter_no_tags="Only process posts that have no tags. Cannot be used with filter_tag."
    )
    @app_commands.autocomplete(tag=tag_autocomplete, filter_tag=tag_autocomplete)
    async def masstag(self, interaction: discord.Interaction, forum: discord.ForumChannel, tag: str, action: Literal['add', 'remove'], filter_name: str = None, filter_tag: str = None, filter_no_tags: bool = False):
        await interaction.response.defer(ephemeral=True, thinking=True)

        if filter_tag and filter_no_tags:
            await interaction.followup.send("❌ You cannot use `filter_tag` and `filter_no_tags` at the same time.", ephemeral=True)
            return

        target_tag = discord.utils.get(forum.available_tags, name=tag)
        if not target_tag:
            await interaction.followup.send(f"❌ Tag `{tag}` not found in the forum `{forum.name}`.", ephemeral=True)
            return

        view = MassTagView()
        queued_message = await interaction.followup.send(
            f"✅ Your request to `{action}` the tag `{tag}` for all posts in `{forum.name}` has been queued.",
            view=view,
            ephemeral=True
        )

        await self.tag_queue.put((interaction, forum, target_tag, action, filter_name, filter_tag, filter_no_tags, view, queued_message))

    @app_commands.command(name="clear", description="Remove all tags from posts in a forum, with optional filters.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        forum="The forum channel to clear tags from.",
        filter_name="Only process posts containing this text in the title.",
        filter_tag="Only process posts that already have this tag."
    )
    @app_commands.autocomplete(filter_tag=tag_autocomplete)
    async def clear_tags(self, interaction: discord.Interaction, forum: discord.ForumChannel, filter_name: str = None, filter_tag: str = None):
        await interaction.response.defer(ephemeral=True, thinking=True)

        view = MassTagView()
        queued_message = await interaction.followup.send(
            f"✅ Your request to clear all tags for posts in `{forum.name}` has been queued.",
            view=view,
            ephemeral=True
        )

        await self.tag_queue.put((interaction, forum, None, 'clear', filter_name, filter_tag, False, view, queued_message))

    async def _process_tagging_task(self, interaction: discord.Interaction, forum: discord.ForumChannel, tag_to_modify: discord.ForumTag, action: str, filter_name: str | None, filter_tag: str | None, filter_no_tags: bool, view: MassTagView, status_msg: discord.WebhookMessage):

        self.active_tasks[interaction.id] = {
            'view': view,
            'status_msg': status_msg,
            'success': 0,
            'failed': 0,
            'skipped': 0,
            'total': 0
        }

        await status_msg.edit(content="⏳ Starting mass tagging task...", view=view)

        try:
            all_threads = [thread async for thread in forum.archived_threads(limit=None)] + forum.threads

            threads_to_process = []
            filter_tag_obj = discord.utils.get(forum.available_tags, name=filter_tag) if filter_tag else None

            for thread in all_threads:
                # Name filter
                if filter_name and filter_name.lower() not in thread.name.lower():
                    continue
                # Tag filter
                if filter_tag_obj and filter_tag_obj not in thread.applied_tags:
                    continue
                # No-tags filter
                if filter_no_tags and thread.applied_tags:
                    continue
                threads_to_process.append(thread)

            threads = threads_to_process
            self.active_tasks[interaction.id]['total'] = len(threads)

            for i, thread in enumerate(threads):
                if view.stop_requested:
                    break

                try:
                    current_tags = list(thread.applied_tags)
                    should_update = False
                    was_archived = thread.archived

                    if action == 'add' and tag_to_modify not in current_tags:
                        current_tags.append(tag_to_modify)
                        should_update = True
                    elif action == 'remove' and tag_to_modify in current_tags:
                        current_tags.remove(tag_to_modify)
                        should_update = True
                    elif action == 'clear' and current_tags:
                        current_tags = []
                        should_update = True

                    if should_update:
                        if was_archived:
                            await thread.edit(archived=False)
                        
                        await thread.edit(applied_tags=current_tags)

                        if was_archived:
                            await thread.edit(archived=True)
                            
                        self.active_tasks[interaction.id]['success'] += 1
                    else:
                        self.active_tasks[interaction.id]['skipped'] += 1

                except discord.Forbidden:
                    self.active_tasks[interaction.id]['failed'] += 1
                except Exception:
                    self.active_tasks[interaction.id]['failed'] += 1
                
                if (i + 1) % 5 == 0:
                    await self._update_progress(interaction.id, i + 1)
                
                await asyncio.sleep(1.5) # Rate limit

        finally:
            await self._finalize_task(interaction.id, view.stop_requested)
            if interaction.id in self.active_tasks:
                del self.active_tasks[interaction.id]

    async def _update_progress(self, interaction_id: int, processed_count: int):
        if interaction_id not in self.active_tasks:
            return
        
        data = self.active_tasks[interaction_id]
        progress = (processed_count / data['total']) * 100 if data['total'] > 0 else 0
        
        await data['status_msg'].edit(
            content=(
                f"⏳ Processing... {progress:.1f}%\n"
                f"✅ Success: {data['success']} | ❌ Failed: {data['failed']} | ⏭️ Skipped: {data['skipped']} | 📊 Total: {data['total']}"
            ),
            view=data['view']
        )

    async def _finalize_task(self, interaction_id: int, was_stopped: bool):
        if interaction_id not in self.active_tasks:
            return

        data = self.active_tasks[interaction_id]
        view = data['view']

        for item in view.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
                item.label = "✅ Complete" if not was_stopped else "⏹️ Stopped"
        
        status_text = "⏹️ Task stopped by user" if was_stopped else "✅ Task complete!"
        final_message = (
            f"{status_text}\n"
            f"- Successfully updated: {data['success']}\n"
            f"- Failed to update: {data['failed']}\n"
            f"- Skipped (no change needed): {data['skipped']}\n"
            f"- Total posts processed: {data['success'] + data['failed'] + data['skipped']}/{data['total']}"
        )

        await data['status_msg'].edit(content=final_message, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(MassTagger(bot))