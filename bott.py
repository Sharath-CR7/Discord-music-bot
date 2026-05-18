import os
import re
import time
import random
import asyncio
import discord
import threading
import yt_dlp
from datetime import timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from discord.ext import commands

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def _run_health():
    for port in [int(os.environ.get("PORT", 3000)), 3001, 3002, 8000]:
        try:
            HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
            break
        except OSError:
            continue

threading.Thread(target=_run_health, daemon=True).start()

FILTERS = {
    "none":       {"af": None,                                  "label": "None",         "emoji": "▶️"},
    "bassboost":  {"af": "bass=g=20",                           "label": "Bass Boost",   "emoji": "🔈"},
    "superbass":  {"af": "bass=g=40",                           "label": "Super Bass",   "emoji": "💥"},
    "nightcore":  {"af": "asetrate=44100*1.25,aresample=44100", "label": "Nightcore",    "emoji": "🌙"},
    "vaporwave":  {"af": "asetrate=44100*0.8,aresample=44100",  "label": "Vaporwave",    "emoji": "🌊"},
    "8d":         {"af": "apulsator=hz=0.08",                   "label": "8D Audio",     "emoji": "🎧"},
    "echo":       {"af": "aecho=0.8:0.88:60:0.4",              "label": "Echo",         "emoji": "📢"},
    "karaoke":    {"af": "stereotools=mlev=0.015",              "label": "Karaoke",      "emoji": "🎤"},
    "robot":      {"af": "asetrate=44100*0.75,aresample=44100", "label": "Robot",        "emoji": "🤖"},
    "underwater": {"af": "aecho=0.5:0.5:20:0.5,bass=g=-10",    "label": "Underwater",   "emoji": "🌊"},
    "treble":     {"af": "treble=g=10",                         "label": "Treble Boost", "emoji": "🎶"},
    "soft":       {"af": "lowpass=f=1000",                      "label": "Soft",         "emoji": "🌙"},
    "earrape":    {"af": "bass=g=25,treble=g=15", "vol_mult": 2.0, "label": "Ear Rape",  "emoji": "💀"},
}

class GuildState:
    def __init__(self):
        self.queue         = []
        self.loop          = False
        self.autoplay      = False
        self.history       = []
        self.np_msg        = None
        self.play_start    = None
        self.paused_at     = None
        self.volume        = 50
        self.current       = None
        self.prog_task     = None
        self.active_filter = "none"
        self._pending_seek = None

    def elapsed(self):
        if self.play_start is None:
            return 0
        if self.paused_at is not None:
            return max(0, int(self.paused_at - self.play_start))
        return max(0, int(time.time() - self.play_start))

    def cancel_progress(self):
        if self.prog_task:
            self.prog_task.cancel()
            self.prog_task = None

_states = {}

def get_state(gid) -> GuildState:
    if gid not in _states:
        _states[gid] = GuildState()
    return _states[gid]

YTDL_OPTS = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

def ytdl_extract(query: str) -> dict:
    if query.startswith("http"):
        return ytdl.extract_info(query, download=False)
    data = ytdl.extract_info(f"ytsearch:{query}", download=False)
    entries = data.get("entries")
    return entries[0] if entries else data

def make_track(data: dict, requester=None) -> dict:
    return {
        "url":       data["url"],
        "title":     data.get("title", "Unknown"),
        "duration":  data.get("duration"),
        "thumbnail": data.get("thumbnail"),
        "webpage":   data.get("webpage_url", ""),
        "uploader":  data.get("uploader", ""),
        "requester": requester,
    }

def build_source(url, seek=0, filter_name="none", volume=50):
    before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if seek > 0:
        before += f" -ss {int(seek)}"
    opts = "-vn"
    f = FILTERS.get(filter_name, {})
    af = f.get("af")
    if af:
        opts += f' -af "{af}"'
    raw = discord.FFmpegPCMAudio(url, before_options=before, options=opts)
    vol = (volume / 100) * f.get("vol_mult", 1.0)
    return discord.PCMVolumeTransformer(raw, volume=vol)

def parse_time(s: str) -> int:
    s = s.strip()
    if ":" in s:
        parts = s.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(parts[0]) * 60 + int(parts[1])
    return int(s)

def fmt_time(sec) -> str:
    if not sec:
        return "Live"
    sec = int(sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"

def make_bar(elapsed, total, length=20) -> str:
    if not total:
        return "▬" * length
    ratio = min(elapsed / total, 1.0)
    pos = int(ratio * length)
    return "▬" * pos + "🔘" + "▬" * (length - pos)

def build_embed(st: GuildState) -> discord.Embed:
    t = st.current
    if not t:
        return discord.Embed(description="Nothing is playing.", color=discord.Color.greyple())
    elapsed  = st.elapsed()
    duration = t.get("duration")
    bar      = make_bar(elapsed, duration)
    f_info   = FILTERS.get(st.active_filter, {})
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"### [{t['title']}]({t['webpage']})" if t.get("webpage") else f"### {t['title']}",
        color=discord.Color.from_rgb(29, 185, 84)
    )
    if t.get("thumbnail"):
        embed.set_image(url=t["thumbnail"])
    embed.add_field(name="", value=f"`{fmt_time(elapsed)}` {make_bar(elapsed, duration)} `{fmt_time(duration)}`", inline=False)
    fi = f_info.get("emoji", "▶️") + " " + f_info.get("label", "None")
    parts = [
        f"🔁 Loop: {'**ON**' if st.loop else 'OFF'}",
        f"✨ Auto: {'**ON**' if st.autoplay else 'OFF'}",
        f"🔊 {st.volume}%",
        f"🎛️ {fi}",
    ]
    embed.add_field(name="", value="  •  ".join(parts), inline=False)
    req = t.get("requester")
    if req and hasattr(req, "display_avatar"):
        embed.set_footer(text=f"Requested by {req}", icon_url=req.display_avatar.url)
    return embed

def autoplay_query(track: dict) -> str:
    title    = track.get("title", "")
    uploader = track.get("uploader", "")
    if " - " in title:
        return f"{title.split(' - ')[0].strip()} best songs"
    if uploader:
        return f"{uploader} music"
    clean = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
    return f"{clean} similar music"

async def play_next(ctx):
    gid = ctx.guild.id
    st  = get_state(gid)
    vc  = ctx.voice_client
    st.cancel_progress()
    if not vc:
        return

    seek = st._pending_seek
    if seek is not None:
        st._pending_seek = None
        track = st.current
        if not track:
            return
        st.play_start = time.time() - seek
        st.paused_at  = None
        try:
            src = build_source(track["url"], seek=seek, filter_name=st.active_filter, volume=st.volume)
            vc.play(src, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        except Exception as e:
            await ctx.send(f"❌ Playback error: `{e}`")
            return
        if st.np_msg:
            try:
                await st.np_msg.edit(embed=build_embed(st), view=MusicView(ctx))
            except Exception:
                pass
        st.prog_task = asyncio.create_task(_progress_loop(ctx))
        return

    if not st.queue:
        if st.autoplay and st.current:
            q = autoplay_query(st.current)
            try:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, lambda: ytdl_extract(q))
                track = make_track(data, st.current.get("requester"))
                if track["title"] != st.current["title"]:
                    st.queue.append(track)
                else:
                    await _end_queue(st); return
            except Exception:
                await _end_queue(st); return
        else:
            await _end_queue(st); return

    track = st.queue.pop(0)
    if st.loop:
        st.queue.append(track)
    st.current    = track
    st.play_start = time.time()
    st.paused_at  = None
    st.history.append(track)

    try:
        src = build_source(track["url"], filter_name=st.active_filter, volume=st.volume)
        vc.play(src, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
    except Exception as e:
        await ctx.send(f"❌ Playback error: `{e}`"); return

    embed = build_embed(st)
    view  = MusicView(ctx)
    if st.np_msg:
        try:
            await st.np_msg.delete()
        except Exception:
            pass
    st.np_msg    = await ctx.send(embed=embed, view=view)
    st.prog_task = asyncio.create_task(_progress_loop(ctx))

async def _end_queue(st: GuildState):
    if st.np_msg:
        try:
            await st.np_msg.edit(embed=discord.Embed(description="✅ Queue finished.", color=discord.Color.green()), view=None)
        except Exception:
            pass
    st.current = None
    st.np_msg  = None

async def _progress_loop(ctx):
    st = get_state(ctx.guild.id)
    while True:
        await asyncio.sleep(5)
        vc = ctx.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            break
        if not st.np_msg or not st.current:
            break
        try:
            await st.np_msg.edit(embed=build_embed(st))
        except Exception:
            break

async def do_seek(ctx, seconds: int):
    st = get_state(ctx.guild.id)
    vc = ctx.voice_client
    if not vc or not st.current:
        return
    dur = st.current.get("duration", 0)
    if dur:
        seconds = min(seconds, max(0, dur - 1))
    st._pending_seek = seconds
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    else:
        st._pending_seek = None

async def do_filter(ctx, filter_name: str):
    st = get_state(ctx.guild.id)
    vc = ctx.voice_client
    if not vc or not st.current:
        return False
    st.active_filter = filter_name
    st._pending_seek = st.elapsed()
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    return True

class SeekModal(discord.ui.Modal, title="⏩ Seek to Position"):
    timestamp = discord.ui.TextInput(label="Timestamp (e.g.  1:30  or  90)", placeholder="Enter a time...", min_length=1, max_length=10)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx

    async def on_submit(self, interaction: discord.Interaction):
        try:
            secs = parse_time(str(self.timestamp.value))
        except (ValueError, IndexError):
            return await interaction.response.send_message("❌ Invalid format. Use `1:30` or `90`.", ephemeral=True)
        await interaction.response.send_message(f"⏩ Jumping to `{fmt_time(secs)}`...", ephemeral=True)
        await do_seek(self.ctx, secs)

class FilterSelect(discord.ui.Select):
    def __init__(self, ctx):
        self.ctx = ctx
        options = [discord.SelectOption(label=v["label"], value=k, emoji=v["emoji"]) for k, v in FILTERS.items()]
        super().__init__(placeholder="🎛️ Audio Filter — select one...", options=options, row=2)

    async def callback(self, interaction: discord.Interaction):
        name = self.values[0]
        await interaction.response.send_message(f"🎛️ Applying **{FILTERS[name]['label']}**...", ephemeral=True)
        ok = await do_filter(self.ctx, name)
        if not ok:
            await interaction.followup.send("❌ Nothing is playing.", ephemeral=True)

class MusicView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.add_item(FilterSelect(ctx))

    def _st(self) -> GuildState:
        return get_state(self.ctx.guild.id)

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        hist = self._st().history
        if len(hist) < 2:
            return await interaction.response.send_message("No previous song.", ephemeral=True)
        hist.pop()
        prev = hist.pop()
        self._st().queue.insert(0, prev)
        if self.ctx.voice_client:
            self.ctx.voice_client.stop()
        await interaction.response.send_message("⏮ Playing previous.", ephemeral=True)

    @discord.ui.button(emoji="⏯", style=discord.ButtonStyle.primary, row=0)
    async def playpause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        vc = self.ctx.voice_client
        if vc and vc.is_paused():
            dur = time.time() - (st.paused_at or time.time())
            st.play_start = (st.play_start or time.time()) + dur
            st.paused_at  = None
            vc.resume()
            await interaction.response.send_message("▶ Resumed", ephemeral=True)
        elif vc and vc.is_playing():
            st.paused_at = time.time()
            vc.pause()
            await interaction.response.send_message("⏸ Paused", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client:
            self.ctx.voice_client.stop()
        await interaction.response.send_message("⏭ Skipped", ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        st.queue.clear()
        st.cancel_progress()
        if self.ctx.voice_client:
            self.ctx.voice_client.stop()
        await interaction.response.send_message("⏹ Stopped & queue cleared", ephemeral=True)

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        q = self._st().queue
        if q:
            random.shuffle(q)
            await interaction.response.send_message("🔀 Queue shuffled!", ephemeral=True)
        else:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)

    @discord.ui.button(label="🔉", style=discord.ButtonStyle.secondary, row=1)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        st.volume = max(0, st.volume - 10)
        vc = self.ctx.voice_client
        if vc and vc.source:
            vc.source.volume = st.volume / 100
        await interaction.response.send_message(f"🔉 Volume: **{st.volume}%**", ephemeral=True)

    @discord.ui.button(label="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        st.volume = min(200, st.volume + 10)
        vc = self.ctx.voice_client
        if vc and vc.source:
            vc.source.volume = st.volume / 100
        await interaction.response.send_message(f"🔊 Volume: **{st.volume}%**", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        st.loop = not st.loop
        await interaction.response.send_message(f"🔁 Loop **{'ON' if st.loop else 'OFF'}**", ephemeral=True)

    @discord.ui.button(label="✨ Auto", style=discord.ButtonStyle.secondary, row=1)
    async def auto_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = self._st()
        st.autoplay = not st.autoplay
        await interaction.response.send_message(f"✨ Autoplay **{'ON' if st.autoplay else 'OFF'}**", ephemeral=True)

    @discord.ui.button(label="⏩ Seek", style=discord.ButtonStyle.secondary, row=1)
    async def seek_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SeekModal(self.ctx))

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ Missing argument. Use `!help` for commands.")
    await ctx.send(f"❌ {error}")

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(title="🎵 Music Bot — Commands", color=discord.Color.from_rgb(29, 185, 84))
    cmds = [
        ("!play `<song/URL>`",    "Search & play a song"),
        ("!playtop `<song>`",     "Add to top of queue"),
        ("!skip",                 "Skip current song"),
        ("!stop",                 "Stop & clear queue"),
        ("!pause / !resume",      "Pause or resume"),
        ("!seek `<1:30>`",        "Jump to timestamp"),
        ("!volume `<0–200>`",     "Set volume level"),
        ("!loop",                 "Toggle loop mode"),
        ("!autoplay",             "Toggle smart autoplay"),
        ("!shuffle",              "Shuffle the queue"),
        ("!queue",                "View the queue"),
        ("!nowplaying",           "Refresh now playing card"),
        ("!history",              "Recently played songs"),
        ("!remove `<#>`",         "Remove song from queue"),
        ("!move `<from>` `<to>`", "Move song in queue"),
        ("!filter `<name>`",      "Apply an audio filter"),
        ("!lyrics `<song>`",      "Get lyrics search link"),
        ("!join / !leave",        "Join or leave voice"),
    ]
    for name, desc in cmds:
        embed.add_field(name=name, value=desc, inline=True)
    embed.add_field(name="🎛️ Filters", value="`none`  `bassboost`  `superbass`  `nightcore`  `vaporwave`  `8d`  `echo`  `karaoke`  `robot`  `underwater`  `treble`  `soft`  `earrape`", inline=False)
    embed.set_footer(text="Tip: Use the ⏩ Seek button on the player card to jump to any position!")
    await ctx.send(embed=embed)

@bot.command()
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await ctx.send("✅ Joined your voice channel!")
    else:
        await ctx.send("❌ You must be in a voice channel.")

@bot.command()
async def leave(ctx):
    st = get_state(ctx.guild.id)
    st.cancel_progress()
    st.queue.clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("👋 Left voice channel.")

@bot.command()
async def play(ctx, *, query: str):
    gid = ctx.guild.id
    st  = get_state(gid)
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("❌ Join a voice channel first!")
    async with ctx.typing():
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl_extract(query))
        except Exception as e:
            return await ctx.send(f"❌ Couldn't find that: `{e}`")
    track = make_track(data, ctx.author)
    st.queue.append(track)
    vc = ctx.voice_client
    if not vc.is_playing() and not vc.is_paused():
        await play_next(ctx)
    else:
        embed = discord.Embed(description=f"➕ Added **[{track['title']}]({track['webpage']})** to queue  •  Position #{len(st.queue)}", color=discord.Color.blurple())
        if track.get("thumbnail"):
            embed.set_thumbnail(url=track["thumbnail"])
        await ctx.send(embed=embed)

@bot.command()
async def playtop(ctx, *, query: str):
    gid = ctx.guild.id
    st  = get_state(gid)
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("❌ Join a voice channel first!")
    async with ctx.typing():
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl_extract(query))
        except Exception as e:
            return await ctx.send(f"❌ Couldn't find that: `{e}`")
    track = make_track(data, ctx.author)
    st.queue.insert(0, track)
    vc = ctx.voice_client
    if not vc.is_playing() and not vc.is_paused():
        await play_next(ctx)
    else:
        await ctx.send(f"⬆️ **{track['title']}** added to top of queue!")

@bot.command()
async def skip(ctx):
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send("⏭ Skipped.")
    else:
        await ctx.send("Nothing is playing.")

@bot.command()
async def stop(ctx):
    st = get_state(ctx.guild.id)
    st.queue.clear()
    st.cancel_progress()
    if ctx.voice_client:
        ctx.voice_client.stop()
    await ctx.send("⏹ Stopped and queue cleared.")

@bot.command()
async def pause(ctx):
    st = get_state(ctx.guild.id)
    vc = ctx.voice_client
    if vc and vc.is_playing():
        st.paused_at = time.time()
        vc.pause()
        await ctx.send("⏸ Paused.")
    else:
        await ctx.send("Nothing is playing.")

@bot.command()
async def resume(ctx):
    st = get_state(ctx.guild.id)
    vc = ctx.voice_client
    if vc and vc.is_paused():
        dur = time.time() - (st.paused_at or time.time())
        st.play_start = (st.play_start or time.time()) + dur
        st.paused_at  = None
        vc.resume()
        await ctx.send("▶ Resumed.")
    else:
        await ctx.send("Not paused.")

@bot.command()
async def volume(ctx, vol: int):
    st = get_state(ctx.guild.id)
    if not 0 <= vol <= 200:
        return await ctx.send("❌ Volume must be between 0 and 200.")
    st.volume = vol
    vc = ctx.voice_client
    if vc and vc.source:
        vc.source.volume = vol / 100
    await ctx.send(f"🔊 Volume set to **{vol}%**")

@bot.command()
async def seek(ctx, timestamp: str):
    st = get_state(ctx.guild.id)
    if not st.current:
        return await ctx.send("Nothing is playing.")
    try:
        secs = parse_time(timestamp)
    except (ValueError, IndexError):
        return await ctx.send("❌ Use format `1:30` or `90` (seconds).")
    await ctx.send(f"⏩ Seeking to `{fmt_time(secs)}`...")
    await do_seek(ctx, secs)

@bot.command(name="filter")
async def filter_cmd(ctx, name: str = None):
    if not name or name.lower() not in FILTERS:
        names = "  ".join(f"`{k}`" for k in FILTERS)
        return await ctx.send(f"🎛️ Available filters:\n{names}")
    st = get_state(ctx.guild.id)
    if not st.current:
        return await ctx.send("Nothing is playing.")
    await ctx.send(f"🎛️ Applying **{FILTERS[name.lower()]['label']}** filter...")
    await do_filter(ctx, name.lower())

@bot.command()
async def loop(ctx):
    st = get_state(ctx.guild.id)
    st.loop = not st.loop
    await ctx.send(f"🔁 Loop is now **{'ON' if st.loop else 'OFF'}**")

@bot.command()
async def autoplay(ctx):
    st = get_state(ctx.guild.id)
    st.autoplay = not st.autoplay
    await ctx.send(f"✨ Autoplay is now **{'ON' if st.autoplay else 'OFF'}**")

@bot.command()
async def shuffle(ctx):
    st = get_state(ctx.guild.id)
    if not st.queue:
        return await ctx.send("Queue is empty.")
    random.shuffle(st.queue)
    await ctx.send("🔀 Queue shuffled!")

@bot.command()
async def queue(ctx):
    st = get_state(ctx.guild.id)
    embed = discord.Embed(title="📜 Queue", color=discord.Color.blurple())
    if st.current:
        embed.add_field(name="🎵 Now Playing", value=f"{st.current['title']} `[{fmt_time(st.current.get('duration'))}]`", inline=False)
    if st.queue:
        items = "\n".join([f"`{i+1}.` {t['title']} `[{fmt_time(t.get('duration'))}]`" for i, t in enumerate(st.queue[:15])])
        if len(st.queue) > 15:
            items += f"\n... and **{len(st.queue) - 15}** more"
        embed.add_field(name="Up Next", value=items, inline=False)
    else:
        embed.add_field(name="Up Next", value="Queue is empty.", inline=False)
    if st.autoplay:
        embed.set_footer(text="✨ Smart Autoplay is ON")
    await ctx.send(embed=embed)

@bot.command()
async def nowplaying(ctx):
    st = get_state(ctx.guild.id)
    if not st.current:
        return await ctx.send("Nothing is playing.")
    st.np_msg = await ctx.send(embed=build_embed(st), view=MusicView(ctx))

@bot.command()
async def history(ctx):
    st = get_state(ctx.guild.id)
    if not st.history:
        return await ctx.send("No history yet.")
    items = "\n".join([f"`{i+1}.` {t['title']}" for i, t in enumerate(st.history[-15:])])
    embed = discord.Embed(title="📖 History", description=items, color=discord.Color.blurple())
    await ctx.send(embed=embed)

@bot.command()
async def remove(ctx, pos: int):
    st = get_state(ctx.guild.id)
    if not st.queue or pos < 1 or pos > len(st.queue):
        return await ctx.send(f"❌ Invalid position. Queue has {len(st.queue)} songs.")
    removed = st.queue.pop(pos - 1)
    await ctx.send(f"🗑️ Removed: **{removed['title']}**")

@bot.command()
async def move(ctx, from_pos: int, to_pos: int):
    st = get_state(ctx.guild.id)
    q  = st.queue
    if not (1 <= from_pos <= len(q)) or not (1 <= to_pos <= len(q)):
        return await ctx.send("❌ Invalid positions.")
    track = q.pop(from_pos - 1)
    q.insert(to_pos - 1, track)
    await ctx.send(f"↕️ Moved **{track['title']}** to position **#{to_pos}**")

@bot.command()
async def lyrics(ctx, *, song: str = None):
    if not song:
        st = get_state(ctx.guild.id)
        if st.current:
            song = st.current["title"]
        else:
            return await ctx.send("❌ Provide a song name or play something first.")
    q = song.replace(" ", "+")
    embed = discord.Embed(description=f"🎤 **[Search \"{song}\" on Genius →](https://genius.com/search?q={q})**", color=discord.Color.yellow())
    await ctx.send(embed=embed)

# =========================
# RUN BOT
# =========================

TOKEN = os.environ.get("TOKEN")
bot.run(TOKEN)
