"""
Discord bot with slash commands for managing tasks.
"""

import asyncio
import logging
import os

import discord
from discord import app_commands

from .dynamo_store import DynamoTaskStore
from .task_store import TaskPriority, TaskStatus
from .watcher import TaskWatcher

log = logging.getLogger(__name__)
NOTIFICATION_CHANNEL_ID = int(os.getenv("NOTIFICATION_CHANNEL_ID", "0"))
DISCORD_ADMIN_ROLE = os.getenv("DISCORD_ADMIN_ROLE", "")


def _require_role():
    """Return a check that requires the configured admin role for mutating commands.

    If DISCORD_ADMIN_ROLE is not set, all users are allowed (no restriction).
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        if not DISCORD_ADMIN_ROLE:
            return True
        if not interaction.guild:
            return False
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            return False
        return any(role.name == DISCORD_ADMIN_ROLE for role in member.roles)

    return app_commands.check(predicate)


class TaskBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)
        self.store = DynamoTaskStore()
        self.watcher = TaskWatcher(self.store, poll_interval=5.0)
        self._register_commands()

    def _register_commands(self):
        store = self.store

        @self.tree.command(name="task-create", description="Create a new task")
        @_require_role()
        @app_commands.describe(
            title="Task title",
            description="Detailed description",
            priority="Priority level",
            tags="Comma-separated tags",
        )
        @app_commands.choices(
            priority=[app_commands.Choice(name=p.value, value=p.value) for p in TaskPriority]
        )
        async def task_create(
            interaction: discord.Interaction,
            title: str,
            description: str = "",
            priority: str = "medium",
            tags: str = "",
        ):
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]
            task = store.create(
                title=title,
                description=description,
                priority=priority,
                created_by=str(interaction.user),
                tags=tag_list,
            )
            _trigger_runner(task.id)
            embed = _task_embed(task, title="Task Created")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="task-list", description="List tasks")
        @app_commands.describe(status="Filter by status")
        @app_commands.choices(
            status=[
                app_commands.Choice(name="all", value="all"),
                *[app_commands.Choice(name=s.value, value=s.value) for s in TaskStatus],
            ]
        )
        async def task_list(interaction: discord.Interaction, status: str = "all"):
            filter_status = None if status == "all" else TaskStatus(status)
            tasks = store.list_tasks(status=filter_status)

            if not tasks:
                await interaction.response.send_message("No tasks found.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"Tasks ({status})",
                color=discord.Color.blue(),
            )
            for t in tasks[:25]:
                icon = _status_icon(t.status)
                prio = _priority_icon(t.priority)
                embed.add_field(
                    name=f"{icon} `{t.id}` {t.title}",
                    value=f"Priority: {prio} {t.priority.value} | Created: {t.created_at[:10]}",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="task-view", description="View task details")
        @app_commands.describe(task_id="The task ID")
        async def task_view(interaction: discord.Interaction, task_id: str):
            task = store.get(task_id)
            if not task:
                await interaction.response.send_message(
                    f"Task `{task_id}` not found.", ephemeral=True
                )
                return
            embed = _task_embed(task, title="Task Details")
            output = store.get_agent_output(task_id)
            if output:
                embed.add_field(name="Agent Result", value=output[:1024], inline=False)
            await interaction.response.send_message(embed=embed)
            if output and len(output) > 1024:
                for i in range(1024, len(output), 2000):
                    await interaction.followup.send(f"```\n{output[i : i + 2000]}\n```")

        @self.tree.command(name="task-status", description="Update task status")
        @_require_role()
        @app_commands.describe(task_id="The task ID", status="New status")
        @app_commands.choices(
            status=[app_commands.Choice(name=s.value, value=s.value) for s in TaskStatus]
        )
        async def task_status(interaction: discord.Interaction, task_id: str, status: str):
            task = store.update_status(task_id, TaskStatus(status))
            if not task:
                await interaction.response.send_message(
                    f"Task `{task_id}` not found.", ephemeral=True
                )
                return
            embed = _task_embed(task, title="Task Updated")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="task-delete", description="Delete a task")
        @_require_role()
        @app_commands.describe(task_id="The task ID")
        async def task_delete(interaction: discord.Interaction, task_id: str):
            deleted = store.delete(task_id)
            if not deleted:
                await interaction.response.send_message(
                    f"Task `{task_id}` not found.", ephemeral=True
                )
                return
            await interaction.response.send_message(f"Task `{task_id}` deleted.")

        @self.tree.command(name="task-sync", description="Sync slash commands (admin)")
        @_require_role()
        async def task_sync(interaction: discord.Interaction):
            synced = await self.tree.sync()
            await interaction.response.send_message(
                f"Synced {len(synced)} commands.", ephemeral=True
            )

    async def setup_hook(self):
        await self.tree.sync()
        log.info("Slash commands synced")

        @self.tree.error
        async def on_app_command_error(
            interaction: discord.Interaction, error: app_commands.AppCommandError
        ):
            if isinstance(error, app_commands.CheckFailure):
                role_name = DISCORD_ADMIN_ROLE or "(not configured)"
                await interaction.response.send_message(
                    "You need the **%s** role to use this command." % role_name,
                    ephemeral=True,
                )
            else:
                log.exception("Slash command error: %s", error)
                if not interaction.response.is_done():
                    await interaction.response.send_message("An error occurred.", ephemeral=True)

    async def on_ready(self):
        log.info("Bot online as %s", self.user)

        self.watcher.on_status_change(self._on_task_status_change)
        asyncio.create_task(self.watcher.start())

    async def _on_task_status_change(self, task, old_status: TaskStatus):
        if not NOTIFICATION_CHANNEL_ID:
            return
        if task.parent_id:
            return
        try:
            channel = await self.fetch_channel(NOTIFICATION_CHANNEL_ID)
        except discord.NotFound:
            log.warning("Notification channel %d not found", NOTIFICATION_CHANNEL_ID)
            return
        except discord.Forbidden:
            log.warning("Bot lacks access to notification channel %d", NOTIFICATION_CHANNEL_ID)
            return

        icon = _status_icon(task.status)
        embed = discord.Embed(
            title=f"{icon} Task Status Changed",
            description=f"**{task.title}** (`{task.id}`)",
            color=_status_color(task.status),
        )
        embed.add_field(name="From", value=old_status.value, inline=True)
        embed.add_field(name="To", value=task.status.value, inline=True)
        if task.created_by:
            embed.add_field(name="Created by", value=task.created_by, inline=False)
        message = await channel.send(embed=embed)

        if task.status == TaskStatus.COMPLETED:
            agent_output = self.store.get_agent_output(task.id)
            if agent_output:
                thread = await message.create_thread(name=f"Agent Result — {task.title[:80]}")
                for chunk in _split_message(agent_output):
                    await thread.send(chunk)


from .web import trigger_runner as _trigger_runner


def _split_message(text: str, limit: int = 1990) -> list:
    """Split text into chunks that fit within Discord's message limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _status_icon(status: TaskStatus) -> str:
    return {
        TaskStatus.PENDING: "\u23f3",
        TaskStatus.IN_PROGRESS: "\U0001f504",
        TaskStatus.COMPLETED: "\u2705",
        TaskStatus.CANCELLED: "\u274c",
    }.get(status, "\u2753")


def _priority_icon(priority: TaskPriority) -> str:
    return {
        TaskPriority.LOW: "\U0001f7e2",
        TaskPriority.MEDIUM: "\U0001f7e1",
        TaskPriority.HIGH: "\U0001f7e0",
        TaskPriority.URGENT: "\U0001f534",
    }.get(priority, "\u26aa")


def _status_color(status: TaskStatus) -> discord.Color:
    return {
        TaskStatus.PENDING: discord.Color.light_grey(),
        TaskStatus.IN_PROGRESS: discord.Color.blue(),
        TaskStatus.COMPLETED: discord.Color.green(),
        TaskStatus.CANCELLED: discord.Color.red(),
    }.get(status, discord.Color.default())


def _task_embed(task, title: str = "Task") -> discord.Embed:
    icon = _status_icon(task.status)
    prio = _priority_icon(task.priority)
    embed = discord.Embed(
        title=f"{icon} {title}",
        description=f"**{task.title}**",
        color=_status_color(task.status),
    )
    embed.add_field(name="ID", value=f"`{task.id}`", inline=True)
    embed.add_field(name="Status", value=task.status.value, inline=True)
    embed.add_field(name="Priority", value=f"{prio} {task.priority.value}", inline=True)
    if task.tags:
        embed.add_field(name="Tags", value=", ".join(task.tags), inline=True)
    if task.description:
        embed.add_field(name="Description", value=task.description[:1024], inline=False)
    embed.set_footer(text=f"Created: {task.created_at} | Updated: {task.updated_at}")
    if task.created_by:
        embed.set_author(name=task.created_by)
    return embed
