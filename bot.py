import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import datetime
import traceback
from typing import Dict, List, Optional
from collections import defaultdict

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ADMIN_USER_IDS = [int(id.strip()) for id in os.getenv('ADMIN_USER_IDS', '').split(',') if id.strip()]

# Railway uses /tmp for ephemeral storage
DATA_DIR = '/tmp/data' if os.path.exists('/tmp') else './data'
CONFIG_FILE = f'{DATA_DIR}/auto_messenger_config.json'
USER_DATA_FILE = f'{DATA_DIR}/user_configs.json'
LOG_FILE = f'{DATA_DIR}/messenger_logs.json'

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# ========== SETUP BOT ==========
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# ========== DATA MANAGERS ==========
class ConfigManager:
    def __init__(self, filename):
        self.filename = filename
        self.data = self.load_data()
    
    def load_data(self):
        """Load data from JSON file"""
        try:
            if os.path.exists(self.filename):
                with open(self.filename, 'r') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Error loading {self.filename}: {e}")
        return {}
    
    def save_data(self):
        """Save data to JSON file"""
        try:
            with open(self.filename, 'w') as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Error saving {self.filename}: {e}")
    
    def get_user_config(self, user_id):
        """Get configuration for a specific user"""
        return self.data.get(str(user_id), {})
    
    def set_user_config(self, user_id, config):
        """Set configuration for a specific user"""
        self.data[str(user_id)] = config
        self.save_data()
    
    def delete_user_config(self, user_id):
        """Delete user configuration"""
        if str(user_id) in self.data:
            del self.data[str(user_id)]
            self.save_data()
            return True
        return False
    
    def get_all_users(self):
        """Get all users with configurations"""
        return self.data

class MessageScheduler:
    def __init__(self):
        self.schedules = {}
        self.active_tasks = {}
        self.config_manager = ConfigManager(CONFIG_FILE)
        self.user_manager = ConfigManager(USER_DATA_FILE)
        self.logs = self.load_logs()
    
    def load_logs(self):
        """Load message logs"""
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r') as f:
                    return json.load(f)
        except Exception:
            pass
        return {"messages": [], "errors": []}
    
    def save_logs(self):
        """Save message logs"""
        try:
            with open(LOG_FILE, 'w') as f:
                json.dump(self.logs, f, indent=4, default=str)
        except Exception as e:
            print(f"Error saving logs: {e}")
    
    def log_message(self, user_id, channel_id, message, status="sent"):
        """Log a message send attempt"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_id": str(user_id),
            "channel_id": str(channel_id),
            "message": message[:100],  # Truncate long messages
            "status": status
        }
        self.logs["messages"].append(log_entry)
        # Keep only last 1000 logs
        if len(self.logs["messages"]) > 1000:
            self.logs["messages"] = self.logs["messages"][-1000:]
        self.save_logs()
    
    def log_error(self, error_message, user_id=None):
        """Log an error"""
        error_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_id": str(user_id) if user_id else "system",
            "error": error_message[:500]
        }
        self.logs["errors"].append(error_entry)
        if len(self.logs["errors"]) > 500:
            self.logs["errors"] = self.logs["errors"][-500:]
        self.save_logs()
    
    def setup_user_schedule(self, user_id, token, channel_id, interval_minutes, message):
        """Setup auto-messaging for a user"""
        config = {
            "token": token,
            "channel_id": str(channel_id),
            "interval": interval_minutes,
            "message": message,
            "enabled": True,
            "created_at": datetime.datetime.now().isoformat(),
            "last_sent": None,
            "total_sent": 0
        }
        
        self.user_manager.set_user_config(user_id, config)
        self.start_user_schedule(user_id)
        return config
    
    def start_user_schedule(self, user_id):
        """Start the scheduling task for a user"""
        config = self.user_manager.get_user_config(user_id)
        if not config or not config.get("enabled", False):
            return
        
        # Cancel existing task if any
        if user_id in self.active_tasks:
            self.active_tasks[user_id].cancel()
        
        # Create new task
        task = asyncio.create_task(self.run_user_schedule(user_id))
        self.active_tasks[user_id] = task
    
    async def run_user_schedule(self, user_id):
        """Run the scheduled messaging for a user"""
        config = self.user_manager.get_user_config(user_id)
        if not config:
            return
        
        interval = config.get("interval", 60)
        channel_id = config.get("channel_id")
        message = config.get("message", "")
        
        while True:
            try:
                await asyncio.sleep(interval * 60)  # Convert minutes to seconds
                
                # Check if still enabled
                config = self.user_manager.get_user_config(user_id)
                if not config or not config.get("enabled", False):
                    break
                
                # Send message
                channel = bot.get_channel(int(channel_id))
                if channel:
                    await channel.send(message)
                    
                    # Update config
                    config["last_sent"] = datetime.datetime.now().isoformat()
                    config["total_sent"] = config.get("total_sent", 0) + 1
                    self.user_manager.set_user_config(user_id, config)
                    
                    # Log success
                    self.log_message(user_id, channel_id, message, "sent")
                else:
                    self.log_message(user_id, channel_id, message, "failed - channel not found")
                    self.log_error(f"Channel {channel_id} not found", user_id)
                    
            except Exception as e:
                error_msg = f"Error in user schedule {user_id}: {str(e)}"
                print(error_msg)
                self.log_error(error_msg, user_id)
                await asyncio.sleep(60)  # Wait before retry
    
    def stop_user_schedule(self, user_id):
        """Stop a user's schedule"""
        if user_id in self.active_tasks:
            self.active_tasks[user_id].cancel()
            del self.active_tasks[user_id]
    
    def get_user_stats(self, user_id):
        """Get statistics for a user"""
        config = self.user_manager.get_user_config(user_id)
        if not config:
            return None
        
        # Count logs for this user
        user_logs = [log for log in self.logs.get("messages", []) 
                    if log.get("user_id") == str(user_id)]
        
        successful = len([log for log in user_logs if log.get("status") == "sent"])
        failed = len([log for log in user_logs if log.get("status") == "failed"])
        
        return {
            "config": config,
            "stats": {
                "total_sent": config.get("total_sent", 0),
                "successful_logs": successful,
                "failed_logs": failed,
                "last_sent": config.get("last_sent"),
                "created_at": config.get("created_at")
            }
        }
    
    def get_all_stats(self):
        """Get statistics for all users"""
        all_users = self.user_manager.get_all_users()
        stats = {}
        
        for user_id_str, config in all_users.items():
            user_id = int(user_id_str)
            user_stats = self.get_user_stats(user_id)
            if user_stats:
                stats[user_id_str] = user_stats
        
        return stats

# Initialize scheduler
scheduler = MessageScheduler()

# ========== EMBED BUILDERS ==========
class EmbedBuilder:
    @staticmethod
    def create_dashboard(user_id, user_stats=None):
        """Create dashboard embed for a user"""
        if not user_stats:
            user_stats = scheduler.get_user_stats(user_id)
        
        embed = discord.Embed(
            title="📊 Auto-Messaging Dashboard",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now()
        )
        
        if user_stats:
            config = user_stats["config"]
            stats = user_stats["stats"]
            
            embed.add_field(
                name="⚙️ Configuration",
                value=f"**Channel:** <#{config['channel_id']}>\n"
                      f"**Interval:** {config['interval']} minutes\n"
                      f"**Status:** {'✅ Enabled' if config['enabled'] else '⏸️ Disabled'}",
                inline=False
            )
            
            embed.add_field(
                name="📈 Statistics",
                value=f"**Total Sent:** {stats['total_sent']}\n"
                      f"**Successful:** {stats['successful_logs']}\n"
                      f"**Failed:** {stats['failed_logs']}\n"
                      f"**Last Sent:** {stats['last_sent'] or 'Never'}",
                inline=True
            )
            
            embed.add_field(
                name="📝 Message Preview",
                value=f"```{config['message'][:200]}...```" if len(config['message']) > 200 
                      else f"```{config['message']}```",
                inline=False
            )
            
            embed.set_footer(text=f"User ID: {user_id}")
        else:
            embed.description = "No auto-messaging configuration found. Use `!setup` to get started."
        
        return embed
    
    @staticmethod
    def create_admin_panel():
        """Create admin panel embed"""
        all_stats = scheduler.get_all_stats()
        
        embed = discord.Embed(
            title="🛠️ Admin Control Panel",
            description=f"**Total Users:** {len(all_stats)}\n"
                       f"**Active Tasks:** {len(scheduler.active_tasks)}",
            color=discord.Color.green(),
            timestamp=datetime.datetime.now()
        )
        
        if all_stats:
            for user_id_str, data in list(all_stats.items())[:10]:  # Show first 10 users
                config = data["config"]
                stats = data["stats"]
                
                status = "🟢" if config.get("enabled") else "🔴"
                user_info = f"{status} **User:** <@{user_id_str}>\n"
                user_info += f"**Channel:** <#{config['channel_id']}>\n"
                user_info += f"**Interval:** {config['interval']}m | **Sent:** {stats['total_sent']}\n"
                user_info += f"**Last:** {stats['last_sent'][:19] if stats['last_sent'] else 'Never'}"
                
                embed.add_field(
                    name=f"User {user_id_str[:8]}...",
                    value=user_info,
                    inline=True
                )
            
            if len(all_stats) > 10:
                embed.add_field(
                    name="📋 More Users",
                    value=f"... and {len(all_stats) - 10} more users",
                    inline=False
                )
        else:
            embed.add_field(
                name="No Users",
                value="No users have configured auto-messaging yet.",
                inline=False
            )
        
        # System stats
        total_logs = len(scheduler.logs.get("messages", []))
        total_errors = len(scheduler.logs.get("errors", []))
        
        embed.add_field(
            name="📊 System Statistics",
            value=f"**Total Messages Logged:** {total_logs}\n"
                  f"**Total Errors:** {total_errors}\n"
                  f"**Bot Uptime:** {format_timedelta(datetime.datetime.now() - bot.start_time)}",
            inline=False
        )
        
        embed.set_footer(text="Admin Panel | Use buttons below to manage")
        return embed
    
    @staticmethod
    def create_setup_form():
        """Create setup form embed"""
        embed = discord.Embed(
            title="⚙️ Auto-Messaging Setup",
            description="Please provide the following information to setup auto-messaging:\n\n"
                       "**1️⃣ Discord Token** (Your bot/user token)\n"
                       "**2️⃣ Channel ID** (Right-click channel → Copy ID)\n"
                       "**3️⃣ Interval** (Minutes between messages)\n"
                       "**4️⃣ Message** (Text to send automatically)\n\n"
                       "Reply to this message with your answers in order, one per line.",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="📝 Example Response",
            value="```\nMTE0ND... (your token)\n123456789012345678\n60\nHello World! This is an auto message!\n```",
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Important Notes",
            value="• Keep your token secure!\n"
                  "• Enable Developer Mode for Channel ID\n"
                  "• Minimum interval: 1 minute\n"
                  "• You can stop anytime with `!stop`",
            inline=False
        )
        
        embed.set_footer(text="Setup will timeout in 5 minutes")
        return embed

# ========== HELPER FUNCTIONS ==========
def format_timedelta(td):
    """Format timedelta to readable string"""
    total_seconds = int(td.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    
    return " ".join(parts) if parts else "0s"

def is_admin(ctx):
    """Check if user is admin"""
    return ctx.author.id in ADMIN_USER_IDS

# ========== BOT EVENTS ==========
@bot.event
async def on_ready():
    """Bot is ready"""
    bot.start_time = datetime.datetime.now()
    print(f"✅ {bot.user} is online!")
    print(f"📊 Connected to {len(bot.guilds)} servers")
    print(f"👑 Admin users: {ADMIN_USER_IDS}")
    
    # Restart all user schedules
    all_users = scheduler.user_manager.get_all_users()
    for user_id_str in all_users.keys():
        scheduler.start_user_schedule(int(user_id_str))
    
    print(f"🔄 Restarted {len(all_users)} user schedules")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!")
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Please wait {error.retry_after:.1f} seconds before using this command again.")
    else:
        error_msg = f"Error in command {ctx.command}: {str(error)}"
        print(error_msg)
        scheduler.log_error(error_msg, ctx.author.id)

# ========== COMMANDS ==========
@bot.command(name='setup')
@commands.cooldown(1, 60, commands.BucketType.user)  # Once per minute per user
async def setup_command(ctx):
    """Start the auto-messaging setup wizard"""
    embed = EmbedBuilder.create_setup_form()
    setup_msg = await ctx.send(embed=embed)
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    
    try:
        # Wait for user response
        response = await bot.wait_for('message', timeout=300.0, check=check)
        
        # Parse response
        lines = response.content.strip().split('\n')
        if len(lines) < 4:
            await ctx.send("❌ Invalid format. Please provide all 4 pieces of information.")
            return
        
        token = lines[0].strip()
        channel_id = lines[1].strip()
        interval = lines[2].strip()
        message = '\n'.join(lines[3:]).strip()
        
        # Validate inputs
        if not token:
            await ctx.send("❌ Token cannot be empty!")
            return
        
        if not channel_id.isdigit():
            await ctx.send("❌ Channel ID must be a number!")
            return
        
        if not interval.isdigit() or int(interval) < 1:
            await ctx.send("❌ Interval must be a number (minimum 1 minute)!")
            return
        
        if not message:
            await ctx.send("❌ Message cannot be empty!")
            return
        
        # Convert to integers
        channel_id_int = int(channel_id)
        interval_int = int(interval)
        
        # Try to setup
        try:
            config = scheduler.setup_user_schedule(
                ctx.author.id,
                token,
                channel_id_int,
                interval_int,
                message
            )
            
            success_embed = discord.Embed(
                title="✅ Setup Complete!",
                description=f"Auto-messaging has been configured successfully.",
                color=discord.Color.green()
            )
            
            success_embed.add_field(
                name="Configuration Details",
                value=f"**Channel:** <#{channel_id_int}>\n"
                      f"**Interval:** {interval_int} minutes\n"
                      f"**Message:** ```{message[:100]}...```",
                inline=False
            )
            
            success_embed.add_field(
                name="Next Steps",
                value="• Messages will start sending automatically\n"
                      "• Use `!dashboard` to view your stats\n"
                      "• Use `!stop` to stop messaging\n"
                      "• Use `!edit` to change settings",
                inline=False
            )
            
            await ctx.send(embed=success_embed)
            
        except Exception as e:
            await ctx.send(f"❌ Error during setup: {str(e)}")
            scheduler.log_error(f"Setup error for {ctx.author.id}: {str(e)}", ctx.author.id)
    
    except asyncio.TimeoutError:
        await ctx.send("⏰ Setup timed out. Please use `!setup` again when ready.")
    except Exception as e:
        await ctx.send(f"❌ Unexpected error: {str(e)}")
        scheduler.log_error(f"Unexpected setup error: {str(e)}", ctx.author.id)

@bot.command(name='dashboard')
async def dashboard_command(ctx):
    """View your auto-messaging dashboard"""
    user_stats = scheduler.get_user_stats(ctx.author.id)
    
    if user_stats:
        embed = EmbedBuilder.create_dashboard(ctx.author.id, user_stats)
        
        # Add buttons/view options
        view = discord.ui.View(timeout=60)
        
        # Refresh button
        refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.blurple, custom_id="refresh")
        async def refresh_callback(interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ This is not your dashboard!", ephemeral=True)
                return
            
            new_stats = scheduler.get_user_stats(ctx.author.id)
            new_embed = EmbedBuilder.create_dashboard(ctx.author.id, new_stats)
            await interaction.response.edit_message(embed=new_embed, view=view)
        
        refresh_btn.callback = refresh_callback
        view.add_item(refresh_btn)
        
        # Stop/Start button
        config = user_stats["config"]
        if config.get("enabled", False):
            stop_btn = discord.ui.Button(label="⏸️ Stop", style=discord.ButtonStyle.red, custom_id="stop")
            async def stop_callback(interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("❌ This is not your dashboard!", ephemeral=True)
                    return
                
                config["enabled"] = False
                scheduler.user_manager.set_user_config(ctx.author.id, config)
                scheduler.stop_user_schedule(ctx.author.id)
                
                await interaction.response.send_message("✅ Auto-messaging stopped!", ephemeral=True)
                
                # Refresh dashboard
                new_stats = scheduler.get_user_stats(ctx.author.id)
                new_embed = EmbedBuilder.create_dashboard(ctx.author.id, new_stats)
                await interaction.message.edit(embed=new_embed, view=view)
            
            stop_btn.callback = stop_callback
            view.add_item(stop_btn)
        else:
            start_btn = discord.ui.Button(label="▶️ Start", style=discord.ButtonStyle.green, custom_id="start")
            async def start_callback(interaction):
                if interaction.user.id != ctx.author.id:
                    await interaction.response.send_message("❌ This is not your dashboard!", ephemeral=True)
                    return
                
                config["enabled"] = True
                scheduler.user_manager.set_user_config(ctx.author.id, config)
                scheduler.start_user_schedule(ctx.author.id)
                
                await interaction.response.send_message("✅ Auto-messaging started!", ephemeral=True)
                
                # Refresh dashboard
                new_stats = scheduler.get_user_stats(ctx.author.id)
                new_embed = EmbedBuilder.create_dashboard(ctx.author.id, new_stats)
                await interaction.message.edit(embed=new_embed, view=view)
            
            start_btn.callback = start_callback
            view.add_item(start_btn)
        
        # Delete button
        delete_btn = discord.ui.Button(label="🗑️ Delete", style=discord.ButtonStyle.gray, custom_id="delete")
        async def delete_callback(interaction):
            if interaction.user.id != ctx.author.id:
                await interaction.response.send_message("❌ This is not your dashboard!", ephemeral=True)
                return
            
            confirm_embed = discord.Embed(
                title="⚠️ Confirm Deletion",
                description="Are you sure you want to delete your auto-messaging configuration?\n"
                           "This action cannot be undone!",
                color=discord.Color.red()
            )
            
            confirm_view = discord.ui.View(timeout=30)
            
            yes_btn = discord.ui.Button(label="✅ Yes, Delete", style=discord.ButtonStyle.red)
            no_btn = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.gray)
            
            async def yes_callback(interaction2):
                scheduler.stop_user_schedule(ctx.author.id)
                scheduler.user_manager.delete_user_config(ctx.author.id)
                await interaction2.response.send_message("✅ Configuration deleted!", ephemeral=True)
                await interaction.message.delete()
            
            async def no_callback(interaction2):
                await interaction2.response.send_message("❌ Deletion cancelled.", ephemeral=True)
            
            yes_btn.callback = yes_callback
            no_btn.callback = no_callback
            
            confirm_view.add_item(yes_btn)
            confirm_view.add_item(no_btn)
            
            await interaction.response.send_message(embed=confirm_embed, view=confirm_view, ephemeral=True)
        
        delete_btn.callback = delete_callback
        view.add_item(delete_btn)
        
        await ctx.send(embed=embed, view=view)
    else:
        embed = discord.Embed(
            title="📊 Dashboard",
            description="You don't have an auto-messaging setup yet.\n"
                       "Use `!setup` to get started!",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

@bot.command(name='admin')
@commands.check(is_admin)
async def admin_command(ctx):
    """Admin control panel (Admin only)"""
    embed = EmbedBuilder.create_admin_panel()
    
    view = discord.ui.View(timeout=60)
    
    # Refresh button
    refresh_btn = discord.ui.Button(label="🔄 Refresh", style=discord.ButtonStyle.blurple)
    async def refresh_callback(interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Admin only!", ephemeral=True)
            return
        
        new_embed = EmbedBuilder.create_admin_panel()
        await interaction.response.edit_message(embed=new_embed, view=view)
    
    refresh_btn.callback = refresh_callback
    view.add_item(refresh_btn)
    
    # View Logs button
    logs_btn = discord.ui.Button(label="📋 View Logs", style=discord.ButtonStyle.gray)
    async def logs_callback(interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Admin only!", ephemeral=True)
            return
        
        # Create logs embed
        logs_embed = discord.Embed(
            title="📋 System Logs",
            color=discord.Color.dark_gray()
        )
        
        recent_messages = scheduler.logs.get("messages", [])[-10:]
        recent_errors = scheduler.logs.get("errors", [])[-10:]
        
        if recent_messages:
            msg_logs = "\n".join([
                f"`{log['timestamp'][11:19]}` {log['status']} <@{log['user_id']}>: {log['message'][:30]}..."
                for log in recent_messages
            ])
            logs_embed.add_field(name="Recent Messages", value=msg_logs or "None", inline=False)
        
        if recent_errors:
            err_logs = "\n".join([
                f"`{log['timestamp'][11:19]}` <@{log['user_id']}>: {log['error'][:50]}..."
                for log in recent_errors
            ])
            logs_embed.add_field(name="Recent Errors", value=err_logs or "None", inline=False)
        
        logs_embed.set_footer(text=f"Total: {len(scheduler.logs.get('messages', []))} messages, "
                                 f"{len(scheduler.logs.get('errors', []))} errors")
        
        await interaction.response.send_message(embed=logs_embed, ephemeral=True)
    
    logs_btn.callback = logs_callback
    view.add_item(logs_btn)
    
    # Force Stop All button
    stop_btn = discord.ui.Button(label="🛑 Stop All", style=discord.ButtonStyle.red)
    async def stop_callback(interaction):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Admin only!", ephemeral=True)
            return
        
        confirm_embed = discord.Embed(
            title="⚠️ Stop All Users",
            description="Stop auto-messaging for ALL users?",
            color=discord.Color.red()
        )
        
        confirm_view = discord.ui.View(timeout=30)
        
        yes_btn = discord.ui.Button(label="✅ Yes", style=discord.ButtonStyle.red)
        no_btn = discord.ui.Button(label="❌ No", style=discord.ButtonStyle.gray)
        
        async def yes_callback(interaction2):
            # Stop all user schedules
            all_users = scheduler.user_manager.get_all_users()
            stopped = 0
            
            for user_id_str, config in all_users.items():
                config["enabled"] = False
                scheduler.user_manager.set_user_config(int(user_id_str), config)
                scheduler.stop_user_schedule(int(user_id_str))
                stopped += 1
            
            await interaction2.response.send_message(
                f"✅ Stopped {stopped} users!", 
                ephemeral=True
            )
            
            # Refresh admin panel
            new_embed = EmbedBuilder.create_admin_panel()
            await interaction.message.edit(embed=new_embed, view=view)
        
        async def no_callback(interaction2):
            await interaction2.response.send_message("❌ Cancelled.", ephemeral=True)
        
        yes_btn.callback = yes_callback
        no_btn.callback = no_callback
        
        confirm_view.add_item(yes_btn)
        confirm_view.add_item(no_btn)
        
        await interaction.response.send_message(embed=confirm_embed, view=confirm_view, ephemeral=True)
    
    stop_btn.callback = stop_callback
    view.add_item(stop_btn)
    
    await ctx.send(embed=embed, view=view)

@bot.command(name='edit')
async def edit_command(ctx):
    """Edit your auto-messaging configuration"""
    user_stats = scheduler.get_user_stats(ctx.author.id)
    
    if not user_stats:
        await ctx.send("❌ You don't have an auto-messaging setup. Use `!setup` first.")
        return
    
    embed = discord.Embed(
        title="✏️ Edit Configuration",
        description="What would you like to edit?\n\n"
                   "**1️⃣** Message content\n"
                   "**2️⃣** Interval time\n"
                   "**3️⃣** Channel ID\n\n"
                   "Reply with the number of your choice.",
        color=discord.Color.gold()
    )
    
    await ctx.send(embed=embed)
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel and m.content in ['1', '2', '3']
    
    try:
        choice_msg = await bot.wait_for('message', timeout=60.0, check=check)
        choice = choice_msg.content
        
        config = user_stats["config"]
        
        if choice == '1':
            await ctx.send("Please send your new message:")
            new_msg = await bot.wait_for(
                'message', 
                timeout=120.0, 
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel
            )
            config["message"] = new_msg.content
            await ctx.send("✅ Message updated!")
        
        elif choice == '2':
            await ctx.send("Please send the new interval (in minutes):")
            interval_msg = await bot.wait_for(
                'message', 
                timeout=60.0, 
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()
            )
            config["interval"] = int(interval_msg.content)
            await ctx.send(f"✅ Interval updated to {config['interval']} minutes!")
        
        elif choice == '3':
            await ctx.send("Please send the new Channel ID:")
            channel_msg = await bot.wait_for(
                'message', 
                timeout=60.0, 
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()
            )
            config["channel_id"] = channel_msg.content
            await ctx.send(f"✅ Channel updated to <#{config['channel_id']}>!")
        
        # Save changes and restart schedule
        scheduler.user_manager.set_user_config(ctx.author.id, config)
        scheduler.stop_user_schedule(ctx.author.id)
        scheduler.start_user_schedule(ctx.author.id)
        
        # Show updated dashboard
        new_stats = scheduler.get_user_stats(ctx.author.id)
        updated_embed = EmbedBuilder.create_dashboard(ctx.author.id, new_stats)
        await ctx.send(embed=updated_embed)
    
    except asyncio.TimeoutError:
        await ctx.send("⏰ Edit timed out.")

@bot.command(name='stop')
async def stop_command(ctx):
    """Stop your auto-messaging"""
    user_stats = scheduler.get_user_stats(ctx.author.id)
    
    if not user_stats:
        await ctx.send("❌ You don't have an active auto-messaging setup.")
        return
    
    config = user_stats["config"]
    config["enabled"] = False
    scheduler.user_manager.set_user_config(ctx.author.id, config)
    scheduler.stop_user_schedule(ctx.author.id)
    
    embed = discord.Embed(
        title="⏸️ Auto-Messaging Stopped",
        description="Your auto-messaging has been stopped.",
        color=discord.Color.orange()
    )
    
    embed.add_field(
        name="Statistics",
        value=f"**Total Messages Sent:** {config.get('total_sent', 0)}\n"
              f"**Last Sent:** {config.get('last_sent', 'Never')}",
        inline=False
    )
    
    embed.set_footer(text="Use !start to resume, or !dashboard to manage")
    await ctx.send(embed=embed)

@bot.command(name='start')
async def start_command(ctx):
    """Start/resume your auto-messaging"""
    user_stats = scheduler.get_user_stats(ctx.author.id)
    
    if not user_stats:
        await ctx.send("❌ You don't have an auto-messaging setup. Use `!setup` first.")
        return
    
    config = user_stats["config"]
    config["enabled"] = True
    scheduler.user_manager.set_user_config(ctx.author.id, config)
    scheduler.start_user_schedule(ctx.author.id)
    
    embed = discord.Embed(
        title="▶️ Auto-Messaging Started",
        description="Your auto-messaging has been started/resumed.",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name="Configuration",
        value=f"**Channel:** <#{config['channel_id']}>\n"
              f"**Interval:** {config['interval']} minutes",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command(name='help')
async def help_command(ctx):
    """Show help message"""
    embed = discord.Embed(
        title="🤖 Auto-Messaging Bot Help",
        description="A complete auto-messaging system with dashboard and admin panel",
        color=discord.Color.purple()
    )
    
    commands_list = [
        ("!setup", "Start setup wizard for auto-messaging"),
        ("!dashboard", "View your dashboard with stats and controls"),
        ("!edit", "Edit your configuration"),
        ("!stop", "Stop your auto-messaging"),
        ("!start", "Start/resume your auto-messaging"),
        ("!help", "Show this help message")
    ]
    
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    if is_admin(ctx):
        admin_commands = [
            ("!admin", "Open admin control panel"),
            ("Admin Panel", "View all users, logs, and system stats")
        ]
        
        embed.add_field(
            name="👑 Admin Commands",
            value="\n".join([f"**{cmd}** - {desc}" for cmd, desc in admin_commands]),
            inline=False
        )
    
    embed.add_field(
        name="⚠️ Important Notes",
        value="• Keep your token secure!\n"
              "• Minimum interval: 1 minute\n"
              "• Use right-click → Copy ID for Channel ID",
        inline=False
    )
    
    embed.set_footer(text="Bot will automatically restart schedules on reboot")
    await ctx.send(embed=embed)

# ========== KEEP ALIVE SETUP ==========
keep_alive()

# ========== RUN BOT ==========
if __name__ == "__main__":
    print("=" * 50)
    print("🤖 Discord Auto-Messaging Bot")
    print("=" * 50)
    print(f"👑 Admin Users: {ADMIN_USER_IDS}")
    print("\n🚀 Starting bot...")
    
    # Create data files if they don't exist
    for filename in [CONFIG_FILE, USER_DATA_FILE, LOG_FILE]:
        if not os.path.exists(filename):
            with open(filename, 'w') as f:
                json.dump({} if filename != LOG_FILE else {"messages": [], "errors": []}, f)
            print(f"Created {filename}")
    
    try:
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Error starting bot: {e}")
        traceback.print_exc()
