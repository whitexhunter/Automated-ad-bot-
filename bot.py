import discord
from discord.ext import commands, tasks
import asyncio
import json
import os
import datetime
import aiohttp
import traceback
from typing import Dict, List, Optional

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', '')  # Bot token for running the bot
ADMIN_USER_IDS = [int(id.strip()) for id in os.getenv('ADMIN_USER_IDS', '').split(',') if id.strip()]

# Storage
DATA_DIR = '/tmp/data' if os.path.exists('/tmp') else './data'
os.makedirs(DATA_DIR, exist_ok=True)

USER_CONFIG_FILE = f'{DATA_DIR}/user_tokens.json'
SCHEDULE_FILE = f'{DATA_DIR}/schedules.json'

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

class UserAccountManager:
    def __init__(self):
        self.user_tokens = self.load_data(USER_CONFIG_FILE)
        self.schedules = self.load_data(SCHEDULE_FILE)
        self.user_clients = {}  # Store user client sessions
        self.running_tasks = {}
    
    def load_data(self, filename):
        if os.path.exists(filename):
            try:
                with open(filename, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def save_data(self, data, filename):
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4, default=str)
    
    def save_user_tokens(self):
        self.save_data(self.user_tokens, USER_CONFIG_FILE)
    
    def save_schedules(self):
        self.save_data(self.schedules, SCHEDULE_FILE)
    
    def add_user_token(self, discord_user_id: int, token: str, channel_id: int):
        """Store user token for auto-messaging"""
        user_key = str(discord_user_id)
        self.user_tokens[user_key] = {
            'token': token,
            'channel_id': channel_id,
            'added_at': datetime.datetime.now().isoformat(),
            'last_used': None,
            'status': 'active'
        }
        self.save_user_tokens()
        return True
    
    def add_schedule(self, discord_user_id: int, interval: int, message: str):
        """Add schedule for user"""
        schedule_id = f"{discord_user_id}_{datetime.datetime.now().timestamp()}"
        
        self.schedules[schedule_id] = {
            'discord_user_id': discord_user_id,
            'interval': interval,
            'message': message,
            'last_sent': None,
            'next_send': datetime.datetime.now().isoformat(),
            'enabled': True,
            'created_at': datetime.datetime.now().isoformat(),
            'total_sent': 0,
            'errors': 0
        }
        self.save_schedules()
        
        # Start the schedule
        self.start_user_schedule(schedule_id)
        return schedule_id
    
    async def create_user_client(self, user_id: str, token: str):
        """Create a discord client for user account"""
        if user_id in self.user_clients:
            try:
                await self.user_clients[user_id].close()
            except:
                pass
        
        # Create minimal intents for user client
        user_intents = discord.Intents.default()
        user_intents.message_content = True
        
        client = discord.Client(intents=user_intents)
        
        @client.event
        async def on_ready():
            print(f"✅ User account {client.user} logged in")
        
        # Store client
        self.user_clients[user_id] = client
        
        # Start client in background
        asyncio.create_task(client.start(token))
        
        # Wait for login
        await asyncio.sleep(5)
        return client
    
    async def send_message_as_user(self, user_id: str, channel_id: int, message: str):
        """Send message using user's account"""
        try:
            if user_id not in self.user_clients:
                token_data = self.user_tokens.get(user_id)
                if not token_data:
                    return False, "No token found"
                
                client = await self.create_user_client(user_id, token_data['token'])
                if not client:
                    return False, "Failed to login"
            
            client = self.user_clients[user_id]
            
            # Get channel and send message
            channel = client.get_channel(channel_id)
            if not channel:
                return False, f"Channel {channel_id} not found or no access"
            
            await channel.send(message)
            
            # Update last used time
            if user_id in self.user_tokens:
                self.user_tokens[user_id]['last_used'] = datetime.datetime.now().isoformat()
                self.save_user_tokens()
            
            return True, "Message sent"
            
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limited
                return False, f"Rate limited. Try again later."
            return False, f"HTTP Error: {e}"
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    def start_user_schedule(self, schedule_id):
        """Start a schedule task"""
        if schedule_id in self.running_tasks:
            self.running_tasks[schedule_id].cancel()
        
        task = asyncio.create_task(self.run_schedule(schedule_id))
        self.running_tasks[schedule_id] = task
    
    async def run_schedule(self, schedule_id):
        """Run scheduled messaging for user"""
        while True:
            try:
                schedule = self.schedules.get(schedule_id)
                if not schedule or not schedule.get('enabled', True):
                    break
                
                # Calculate next send time
                next_send = datetime.datetime.fromisoformat(schedule['next_send'])
                now = datetime.datetime.now()
                
                if now >= next_send:
                    # Time to send message
                    user_id = str(schedule['discord_user_id'])
                    user_data = self.user_tokens.get(user_id)
                    
                    if user_data:
                        channel_id = user_data['channel_id']
                        message = schedule['message']
                        
                        success, result = await self.send_message_as_user(
                            user_id, 
                            channel_id, 
                            message
                        )
                        
                        if success:
                            schedule['total_sent'] = schedule.get('total_sent', 0) + 1
                            print(f"✅ Sent message for user {user_id}")
                        else:
                            schedule['errors'] = schedule.get('errors', 0) + 1
                            print(f"❌ Failed for user {user_id}: {result}")
                        
                        # Update schedule
                        interval_minutes = schedule['interval']
                        schedule['last_sent'] = now.isoformat()
                        schedule['next_send'] = (now + datetime.timedelta(minutes=interval_minutes)).isoformat()
                        self.schedules[schedule_id] = schedule
                        self.save_schedules()
                    
                    else:
                        print(f"⚠️ No token found for user {user_id}")
                        await asyncio.sleep(60)
                
                # Wait before checking again
                await asyncio.sleep(30)
                
            except Exception as e:
                print(f"❌ Error in schedule {schedule_id}: {e}")
                await asyncio.sleep(60)
    
    def stop_schedule(self, schedule_id):
        """Stop a schedule"""
        if schedule_id in self.running_tasks:
            self.running_tasks[schedule_id].cancel()
            del self.running_tasks[schedule_id]
            return True
        return False
    
    def get_user_schedules(self, discord_user_id: int):
        """Get all schedules for a user"""
        return {k: v for k, v in self.schedules.items() if str(discord_user_id) == str(v.get('discord_user_id'))}
    
    def get_user_info(self, discord_user_id: int):
        """Get user's token info"""
        return self.user_tokens.get(str(discord_user_id))

# Initialize manager
manager = UserAccountManager()

@bot.event
async def on_ready():
    print(f"✅ Bot {bot.user} is online!")
    print(f"📊 Connected to {len(bot.guilds)} servers")
    
    # Restart all schedules
    for schedule_id, schedule in manager.schedules.items():
        if schedule.get('enabled', True):
            manager.start_user_schedule(schedule_id)
    
    print(f"🔄 Restarted {len(manager.schedules)} schedules")

@bot.event 
async def on_message(message):
    # Ignore bot's own messages to prevent loops
    if message.author == bot.user:
        return
    await bot.process_commands(message)

# ========== COMMANDS ==========

@bot.command(name='autotoken')
@commands.is_owner()  # Only bot owner can use
async def auto_token_command(ctx, email: str = None, password: str = None):
    """Automatically get Discord token (OWNER ONLY)"""
    if not email or not password:
        embed = discord.Embed(
            title="🔒 Auto Token Getter",
            description="**Usage:** `!autotoken email password`\n\n"
                       "⚠️ **WARNING:** This sends your credentials to the bot!\n"
                       "Only use with a TEMPORARY password!",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    
    # Delete command for security
    try:
        await ctx.message.delete()
    except:
        pass
    
    # Send processing message
    processing_msg = await ctx.send("🔄 Getting token... (This takes 30-60 seconds)")
    
    try:
        # Import Selenium token getter
        from token_getter import DiscordTokenGetter
        
        # Run in thread to avoid blocking
        def get_token_sync():
            getter = DiscordTokenGetter(headless=True)
            return getter.get_token(email, password)
        
        # Execute
        token = await bot.loop.run_in_executor(None, get_token_sync)
        
        if token:
            # Send token via DM for security
            try:
                await ctx.author.send(f"✅ Token obtained: `{token[:30]}...`")
                
                # Update user config if exists
                user_key = str(ctx.author.id)
                if user_key in manager.user_tokens:
                    manager.user_tokens[user_key]['token'] = token
                    manager.save_user_tokens()
                    await ctx.author.send("✅ Token automatically updated in your config!")
                
                await processing_msg.edit(content="✅ Token sent to your DMs!")
                
            except discord.Forbidden:
                await processing_msg.edit(content="❌ Cannot DM you. Enable DMs from server members.")
        else:
            await processing_msg.edit(content="❌ Failed to get token. Check credentials.")
            
    except ImportError:
        await processing_msg.edit(content="❌ Selenium not installed. Check requirements.txt")
    except Exception as e:
        await processing_msg.edit(content=f"❌ Error: {str(e)[:100]}")

@bot.command(name='gettokenhelp')
async def token_help_command(ctx):
    """Show token getting methods"""
    embed = discord.Embed(
        title="🔑 Token Getting Methods",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="🤖 Auto Method",
        value="`!autotoken email password`\n"
              "Bot gets token automatically\n"
              "**Requires:** Temporary password",
        inline=False
    )
    
    embed.add_field(
        name="📱 Manual Method",
        value="1. Open Discord in browser\n"
              "2. F12 → Console tab\n"
              "3. Paste JavaScript code\n"
              "4. Copy token\n"
              "5. Use `!updatetoken`",
        inline=False
    )
    
    embed.add_field(
        name="⚠️ Security Tips",
        value="• Use temporary password\n"
              "• Enable 2FA after\n"
              "• Never share tokens\n"
              "• Change password regularly",
        inline=False
    )
    
    await ctx.send(embed=embed)
    
@bot.command(name='setup')
@commands.cooldown(1, 60, commands.BucketType.user)
async def setup_command(ctx):
    """Setup auto-messaging with your Discord account token"""
    # Check if user already has token
    user_info = manager.get_user_info(ctx.author.id)
    
    if user_info:
        embed = discord.Embed(
            title="⚠️ Already Setup",
            description="You already have an account linked.\nUse `!mystats` to view or `!remove` to start over.",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="🔑 Auto-Messaging Setup",
        description="**Provide these details (one per line):**\n\n"
                   "1️⃣ **Your Discord Account Token**\n"
                   "   *(Get from browser console)*\n\n"
                   "2️⃣ **Channel ID** where to send messages\n"
                   "   *(Right-click channel → Copy ID)*\n\n"
                   "3️⃣ **Message Interval** in minutes\n\n"
                   "4️⃣ **Message** to send automatically\n\n"
                   "**Example:**\n```\nyour_discord_token_here\n123456789012345678\n30\nJoin our amazing community!\n```",
        color=discord.Color.blue()
    )
    
    embed.set_footer(text="⚠️ Never share your token with anyone!")
    setup_msg = await ctx.send(embed=embed)
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    
    try:
        response = await bot.wait_for('message', timeout=180.0, check=check)
        
        # Parse response
        lines = response.content.strip().split('\n')
        if len(lines) < 4:
            await ctx.send("❌ Need all 4 items: token, channel_id, interval, message")
            return
        
        user_token = lines[0].strip()
        channel_id = lines[1].strip()
        interval = lines[2].strip()
        message_text = '\n'.join(lines[3:]).strip()
        
        # Validate
        if not user_token:
            await ctx.send("❌ Token cannot be empty!")
            return
        
        if not channel_id.isdigit():
            await ctx.send("❌ Channel ID must be a number!")
            return
        
        if not interval.isdigit() or int(interval) < 5:
            await ctx.send("❌ Interval must be at least 5 minutes!")
            return
        
        if not message_text:
            await ctx.send("❌ Message cannot be empty!")
            return
        
        # Convert
        channel_id_int = int(channel_id)
        interval_int = int(interval)
        
        # Test the token
        testing_embed = discord.Embed(
            title="🔄 Testing your token...",
            description="Please wait while we verify your credentials.",
            color=discord.Color.yellow()
        )
        testing_msg = await ctx.send(embed=testing_embed)
        
        try:
            # Create test client
            test_intents = discord.Intents.default()
            test_client = discord.Client(intents=test_intents)
            
            login_success = False
            @test_client.event
            async def on_ready():
                nonlocal login_success
                login_success = True
            
            # Try to login
            login_task = asyncio.create_task(test_client.start(user_token))
            await asyncio.sleep(5)
            
            if login_success:
                await test_client.close()
                
                # Save user token
                manager.add_user_token(ctx.author.id, user_token, channel_id_int)
                
                # Create schedule
                schedule_id = manager.add_schedule(ctx.author.id, interval_int, message_text)
                
                success_embed = discord.Embed(
                    title="✅ Setup Complete!",
                    description=f"Auto-messaging has been configured successfully.\n"
                              f"Messages will be sent from **your account** every **{interval_int} minutes**.",
                    color=discord.Color.green()
                )
                
                success_embed.add_field(
                    name="📋 Details",
                    value=f"**Channel:** <#{channel_id_int}>\n"
                          f"**Interval:** {interval_int} minutes\n"
                          f"**Message:** ```{message_text[:100]}...```\n"
                          f"**Schedule ID:** `{schedule_id}`",
                    inline=False
                )
                
                success_embed.add_field(
                    name="🛠️ Commands",
                    value="• `!mystats` - View your stats\n"
                          "• `!mylogs` - View message logs\n"
                          "• `!pause` - Pause messaging\n"
                          "• `!resume` - Resume messaging\n"
                          "• `!remove` - Remove your setup",
                    inline=False
                )
                
                await testing_msg.edit(embed=success_embed)
                
            else:
                await test_client.close()
                await testing_msg.edit(embed=discord.Embed(
                    title="❌ Invalid Token",
                    description="Could not login with provided token.\n"
                              "Make sure:\n"
                              "1. Token is correct\n"
                              "2. Account is not 2FA protected\n"
                              "3. Token hasn't been revoked",
                    color=discord.Color.red()
                ))
                
        except Exception as e:
            await testing_msg.edit(embed=discord.Embed(
                title="❌ Login Failed",
                description=f"Error: {str(e)[:100]}",
                color=discord.Color.red()
            ))
    
    except asyncio.TimeoutError:
        await ctx.send("⏰ Setup timed out. Please try again.")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")

@bot.command(name='mystats')
async def mystats_command(ctx):
    """View your auto-messaging stats"""
    user_info = manager.get_user_info(ctx.author.id)
    user_schedules = manager.get_user_schedules(ctx.author.id)
    
    if not user_info:
        embed = discord.Embed(
            title="📊 My Stats",
            description="You haven't setup auto-messaging yet.\nUse `!setup` to get started.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
        return
    
    embed = discord.Embed(
        title="📊 Your Auto-Messaging Stats",
        color=discord.Color.green()
    )
    
    # User info
    added_at = user_info.get('added_at', 'Unknown')
    if added_at != 'Unknown':
        added_at = datetime.datetime.fromisoformat(added_at).strftime("%Y-%m-%d %H:%M")
    
    embed.add_field(
        name="🔑 Account Info",
        value=f"**Channel:** <#{user_info['channel_id']}>\n"
              f"**Setup Date:** {added_at}\n"
              f"**Status:** {user_info.get('status', 'active')}",
        inline=False
    )
    
    # Schedule info
    if user_schedules:
        total_sent = sum(s.get('total_sent', 0) for s in user_schedules.values())
        total_errors = sum(s.get('errors', 0) for s in user_schedules.values())
        
        embed.add_field(
            name="📈 Statistics",
            value=f"**Total Schedules:** {len(user_schedules)}\n"
                  f"**Messages Sent:** {total_sent}\n"
                  f"**Errors:** {total_errors}",
            inline=True
        )
        
        # Show first schedule
        first_schedule = list(user_schedules.values())[0]
        last_sent = first_schedule.get('last_sent', 'Never')
        if last_sent != 'Never':
            last_sent = datetime.datetime.fromisoformat(last_sent).strftime("%Y-%m-%d %H:%M")
        
        embed.add_field(
            name="🔄 Active Schedule",
            value=f"**Interval:** {first_schedule['interval']} minutes\n"
                  f"**Last Sent:** {last_sent}\n"
                  f"**Status:** {'✅ Active' if first_schedule.get('enabled', True) else '⏸️ Paused'}",
            inline=True
        )
        
        # Message preview
        msg_preview = first_schedule['message']
        if len(msg_preview) > 100:
            msg_preview = msg_preview[:100] + "..."
        
        embed.add_field(
            name="💬 Message",
            value=f"```{msg_preview}```",
            inline=False
        )
    else:
        embed.add_field(
            name="⚠️ No Active Schedules",
            value="You have no active messaging schedules.",
            inline=False
        )
    
    embed.set_footer(text=f"User ID: {ctx.author.id}")
    await ctx.send(embed=embed)

@bot.command(name='pause')
async def pause_command(ctx):
    """Pause your auto-messaging"""
    user_schedules = manager.get_user_schedules(ctx.author.id)
    
    if not user_schedules:
        await ctx.send("❌ No active schedules found.")
        return
    
    paused = 0
    for schedule_id, schedule in user_schedules.items():
        if schedule.get('enabled', True):
            schedule['enabled'] = False
            manager.schedules[schedule_id] = schedule
            manager.stop_schedule(schedule_id)
            paused += 1
    
    manager.save_schedules()
    
    embed = discord.Embed(
        title="⏸️ Auto-Messaging Paused",
        description=f"Paused {paused} schedule(s).\nUse `!resume` to start again.",
        color=discord.Color.orange()
    )
    await ctx.send(embed=embed)

@bot.command(name='resume')
async def resume_command(ctx):
    """Resume your auto-messaging"""
    user_schedules = manager.get_user_schedules(ctx.author.id)
    
    if not user_schedules:
        await ctx.send("❌ No schedules found.")
        return
    
    resumed = 0
    for schedule_id, schedule in user_schedules.items():
        if not schedule.get('enabled', True):
            schedule['enabled'] = True
            schedule['next_send'] = datetime.datetime.now().isoformat()
            manager.schedules[schedule_id] = schedule
            manager.start_user_schedule(schedule_id)
            resumed += 1
    
    manager.save_schedules()
    
    embed = discord.Embed(
        title="▶️ Auto-Messaging Resumed",
        description=f"Resumed {resumed} schedule(s).",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='remove')
async def remove_command(ctx):
    """Remove your auto-messaging setup"""
    # Confirm
    embed = discord.Embed(
        title="⚠️ Confirm Removal",
        description="Are you sure you want to remove your auto-messaging setup?\n"
                   "This will delete your token and stop all messages.",
        color=discord.Color.red()
    )
    
    confirm_msg = await ctx.send(embed=embed)
    
    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel
    
    try:
        response = await bot.wait_for('message', timeout=30.0, check=check)
        
        if response.content.lower() == 'confirm':
            # Remove user token
            user_key = str(ctx.author.id)
            if user_key in manager.user_tokens:
                del manager.user_tokens[user_key]
                manager.save_user_tokens()
            
            # Remove and stop schedules
            user_schedules = manager.get_user_schedules(ctx.author.id)
            for schedule_id in user_schedules.keys():
                manager.stop_schedule(schedule_id)
                if schedule_id in manager.schedules:
                    del manager.schedules[schedule_id]
            
            manager.save_schedules()
            
            # Close user client if exists
            if user_key in manager.user_clients:
                try:
                    await manager.user_clients[user_key].close()
                    del manager.user_clients[user_key]
                except:
                    pass
            
            embed = discord.Embed(
                title="🗑️ Setup Removed",
                description="Your auto-messaging setup has been completely removed.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
        else:
            await ctx.send("❌ Removal cancelled.")
    
    except asyncio.TimeoutError:
        await ctx.send("⏰ Confirmation timed out.")

@bot.command(name='admin')
@commands.check(lambda ctx: ctx.author.id in ADMIN_USER_IDS)
async def admin_command(ctx):
    """Admin panel"""
    total_users = len(manager.user_tokens)
    total_schedules = len(manager.schedules)
    active_schedules = len([s for s in manager.schedules.values() if s.get('enabled', True)])
    
    embed = discord.Embed(
        title="🛠️ Admin Control Panel",
        color=discord.Color.purple()
    )
    
    embed.add_field(
        name="📊 System Stats",
        value=f"**Total Users:** {total_users}\n"
              f"**Total Schedules:** {total_schedules}\n"
              f"**Active Schedules:** {active_schedules}\n"
              f"**Running Tasks:** {len(manager.running_tasks)}",
        inline=False
    )
    
    # Show recent users
    recent_users = list(manager.user_tokens.items())[-5:]
    if recent_users:
        users_list = ""
        for user_id, data in recent_users:
            users_list += f"• <@{user_id}> - <#{data['channel_id']}>\n"
        
        embed.add_field(
            name="👥 Recent Users",
            value=users_list,
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='help')
async def help_command(ctx):
    """Show help message"""
    embed = discord.Embed(
        title="🤖 Auto-Messaging Bot Help",
        description="Send messages automatically from YOUR Discord account",
        color=discord.Color.blue()
    )
    
    commands = [
        ("!setup", "Setup auto-messaging with your account token"),
        ("!mystats", "View your messaging statistics"),
        ("!pause", "Pause your auto-messaging"),
        ("!resume", "Resume your auto-messaging"),
        ("!remove", "Remove your setup completely"),
        ("!help", "Show this help message")
    ]
    
    for cmd, desc in commands:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    if ctx.author.id in ADMIN_USER_IDS:
        embed.add_field(name="!admin", value="Admin control panel", inline=False)
    
    embed.set_footer(text="⚠️ Keep your token secure! Never share it.")
    await ctx.send(embed=embed)

# ========== ERROR HANDLING ==========
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Please wait {error.retry_after:.1f} seconds before using this command again.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("❌ You don't have permission to use this command.")
    else:
        print(f"Command error: {error}")

# ========== RUN BOT ==========
if __name__ == "__main__":
    print("=" * 50)
    print("🤖 Discord Auto-Messaging Bot")
    print("=" * 50)
    print(f"👑 Admin Users: {ADMIN_USER_IDS}")
    print(f"📁 Data Directory: {DATA_DIR}")
    print(f"📊 Loaded {len(manager.user_tokens)} user tokens")
    print(f"📊 Loaded {len(manager.schedules)} schedules")
    print("\n🚀 Starting bot...")
    
    # Create data files if they don't exist
    for filename in [USER_CONFIG_FILE, SCHEDULE_FILE]:
        if not os.path.exists(filename):
            with open(filename, 'w') as f:
                json.dump({}, f)
            print(f"📄 Created {filename}")
    
    try:
        bot.run(BOT_TOKEN)
    except discord.LoginFailure:
        print("❌ Invalid bot token. Check DISCORD_BOT_TOKEN environment variable.")
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
