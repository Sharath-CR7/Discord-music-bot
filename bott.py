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

# ── Funny roast lines used by !roastme ──────────────────────────────────────
ROASTS = [
    "Your taste in music is like your personality — painfully average.",
    "I've seen better queues at a DMV.",
    "You play the same song on loop more than my grandma plays bingo.",
    "Even Spotify's algorithm gave up trying to understand you.",
    "Your playlist has more skips than a broken DVD.",
    "I've heard better music from a dial-up modem.",
    "You added 47 songs to the queue and then left the voice channel. Classic.",
    "Your music taste called. It wants therapy.",
    "You really queued a 4-hour lo-fi stream at 3am. We need to talk.",
    "Even the earrape filter sounds better than your choices.",
    "You use the bass boost filter on Ed Sheeran. There are no words.",
    "Your vibe is giving 'person who types 'lol' without actually laughing'.",
]

# ── Funny compliments for !vibe ───────────────────────────────────────────────
VIBES = [
    "Absolute legend. This track is peak human achievement.",
    "Certified banger. The council approves.",
    "This song just added 3 years to my life expectancy.",
    "A+ taste. You're the main character today.",
    "Your ancestors are proud of this queue.",
    "This slaps harder than my mom when I got a C in math.",
    "Even Gordon Ramsay would say this is delicious.",
    "NASA wants to know your location so they can launch this into space.",
    "10/10 — would autoplay again.",
    "Your queue is so clean it sparks joy. Marie Kondo is crying.",
]

# ── 8-ball responses ──────────────────────────────────────────────────────────
EIGHT_BALL = [
    "It is certain. 🎱", "Without a doubt. 🎱", "Yes, definitely. 🎱",
    "You may rely on it. 🎱", "As I see it, yes. 🎱", "Most likely. 🎱",
    "Outlook good. 🎱", "Signs point to yes. 🎱", "Reply hazy, try again. 🎱",
    "Ask again later. 🎱", "Better not tell you now. 🎱", "Cannot predict now. 🎱",
    "Don't count on it. 🎱", "My reply is no. 🎱", "My sources say no. 🎱",
    "Outlook not so good. 🎱", "Very doubtful. 🎱",
]

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
        # For autoplay: track titles already played/tried so we don't repeat
        self._ap_seen      = set()

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

YTDL_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "extract_flat": False,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    }
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

def ytdl_extract(query: str) -> dict:
    if query.startswith("http"):
        return ytdl.extract_info(query, download=False)
    data = ytdl.extract_info(f"ytsearch:{query}", download=False)
    entries = data.get("entries")
    return entries[0] if entries else data

def ytdl_extract_many(query: str, count: int = 5) -> list:
    """Return up to `count` results for a search query."""
    data = ytdl.extract_info(f"ytsearch{count}:{query}", download=False)
    return data.get("entries", [])

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
        return discord.Embed(
            title="🎵 GLITCH MUSIC",
            description="No music is currently playing.",
            color=0xff0000
        )

    elapsed = st.elapsed()
    duration = t.get("duration")

    embed = discord.Embed(
        title="🎧 GLITCH MUSIC PLAYER",
        description=f"## [{t['title']}]({t['webpage']})",
        color=0xff0000
    )

    if t.get("thumbnail"):
        embed.set_image(url=t["thumbnail"])

    embed.add_field(
        name="📊 Progress",
        value=f"`{fmt_time(elapsed)}` {make_bar(elapsed, duration)} `{fmt_time(duration)}`",
        inline=False
    )

    embed.add_field(
        name="🎚 Audio Settings",
        value=(
            f"🔊 Volume: `{st.volume}%`\n"
            f"🎛 Filter: `{FILTERS[st.active_filter]['label']}`"
        ),
        inline=True
    )

    embed.add_field(
        name="⚙️ Modes",
        value=(
            f"🔁 Loop: `{'ON' if st.loop else 'OFF'}`\n"
            f"✨ Autoplay: `{'ON' if st.autoplay else 'OFF'}`"
        ),
        inline=True
    )

    embed.add_field(
        name="📜 Queue",
        value=f"`{len(st.queue)}` songs waiting",
        inline=False
    )

    embed.add_field(
        name="🎤 Artist / Uploader",
        value=f"`{t.get('uploader', 'Unknown')}`",
        inline=False
    )

    req = t.get("requester")
    if req and hasattr(req, "display_avatar"):
        embed.set_footer(
            text=f"Requested by {req} • GLITCH MATRIX",
            icon_url=req.display_avatar.url
        )
    else:
        embed.set_footer(text="GLITCH MATRIX • Premium Music Experience")

    embed.set_author(
        name="Now Playing",
        icon_url=bot.user.display_avatar.url
    )

    return embed

def autoplay_query(track: dict) -> str:
    """Build a varied search query so autoplay doesn't repeat."""
    title    = track.get("title", "")
    uploader = track.get("uploader", "")
    # strip tags like (Official Video), [Lyrics] etc.
    clean = re.sub(r"\(.*?\)|\[.*?\]", "", title).strip()
    if " - " in clean:
        artist, song = clean.split(" - ", 1)
        queries = [
            f"{artist.strip()} best songs",
            f"{song.strip()} similar songs",
            f"{artist.strip()} mix",
        ]
    elif uploader:
        queries = [
            f"{uploader} top songs",
            f"{uploader} music mix",
            f"{clean} similar",
        ]
    else:
        queries = [
            f"{clean} similar music",
            f"{clean} mix",
            f"songs like {clean}",
        ]
    return random.choice(queries)

# ═══════════════════════════════════════════════════════════════
#  CORE PLAYBACK  (BUG-FIXED)
# ═══════════════════════════════════════════════════════════════

async def play_next(ctx):
    gid = ctx.guild.id
    st  = get_state(gid)
    vc  = ctx.voice_client
    st.cancel_progress()
    if not vc:
        return

    # ── Handle pending seek (filter change / seek command) ───────────────────
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

    # ── Loop mode: re-queue current track ONLY when queue is otherwise empty ─
    # BUG FIX: previously loop re-appended before checking autoplay,
    # causing the same track to replay even with loop OFF.
    if st.loop and st.current and not st.queue:
        st.queue.append(st.current)

    # ── Queue empty → try autoplay ────────────────────────────────────────────
    if not st.queue:
        if st.autoplay and st.current:
            prev_title = st.current.get("title", "")
            # BUG FIX: fetch multiple results and pick the first unseen one
            q = autoplay_query(st.current)
            found = False
            try:
                loop = asyncio.get_event_loop()
                results = await loop.run_in_executor(None, lambda: ytdl_extract_many(q, 5))
                for entry in results:
                    if not entry:
                        continue
                    t_title = entry.get("title", "")
                    if t_title and t_title != prev_title and t_title not in st._ap_seen:
                        track = make_track(entry, st.current.get("requester"))
                        st._ap_seen.add(t_title)
                        st.queue.append(track)
                        found = True
                        break
            except Exception:
                pass
            if not found:
                # Widen the search with a different query
                try:
                    q2 = autoplay_query(st.current)
                    results2 = await loop.run_in_executor(None, lambda: ytdl_extract_many(q2, 5))
                    for entry in results2:
                        t_title = entry.get("title", "") if entry else ""
                        if t_title and t_title not in st._ap_seen and t_title != prev_title:
                            track = make_track(entry, st.current.get("requester"))
                            st._ap_seen.add(t_title)
                            st.queue.append(track)
                            found = True
                            break
                except Exception:
                    pass
            if not found:
                await _end_queue(st, ctx)
                return
        else:
            await _end_queue(st, ctx)
            return

    # ── Pop next track ────────────────────────────────────────────────────────
    track = st.queue.pop(0)

    # BUG FIX: do NOT re-append here for loop — handled above so loop only
    # triggers when the queue would otherwise be empty, not every track.
    st.current    = track
    st.play_start = time.time()
    st.paused_at  = None
    # Only add to history if it's a new play (not a loop repeat of itself)
    if not st.history or st.history[-1].get("title") != track.get("title"):
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

async def _end_queue(st: GuildState, ctx=None):
    # Clear autoplay seen set so it can discover new songs next session
    st._ap_seen.clear()
    if st.np_msg:
        try:
            await st.np_msg.edit(
                embed=discord.Embed(description="✅ Queue finished. Add more songs or enable autoplay!", color=discord.Color.green()),
                view=None
            )
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

# ═══════════════════════════════════════════════════════════════
#  UI COMPONENTS
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
#  EVENTS
# ═══════════════════════════════════════════════════════════════

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

# ═══════════════════════════════════════════════════════════════
#  MUSIC COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.send("❌ You must be in a voice channel.")
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.channel == channel:
            return await ctx.send("✅ I'm already in your voice channel!")
        await ctx.voice_client.move_to(channel)
        return await ctx.send(f"🔄 Moved to **{channel.name}**")
    await channel.connect()
    await ctx.send(f"✅ Joined **{channel.name}**")

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
    status = "ON" if st.autoplay else "OFF"
    msg = f"✨ Autoplay is now **{status}**"
    if st.autoplay:
        msg += "\n> I'll automatically find similar songs when the queue runs out!"
    await ctx.send(msg)

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
    footer = []
    if st.loop:     footer.append("🔁 Loop ON")
    if st.autoplay: footer.append("✨ Autoplay ON")
    if footer:
        embed.set_footer(text="  •  ".join(footer))
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

# ═══════════════════════════════════════════════════════════════
#  🎉 FUNNY / FUN COMMANDS
# ═══════════════════════════════════════════════════════════════

@bot.command()
async def roastme(ctx):
    """Get roasted by the bot."""
    roast = random.choice(ROASTS)
    embed = discord.Embed(
        title=f"🔥 Roasting {ctx.author.display_name}...",
        description=roast,
        color=discord.Color.orange()
    )
    embed.set_footer(text="It's just a joke... mostly. 😈")
    await ctx.send(embed=embed)

@bot.command()
async def vibe(ctx):
    """Get a random vibe check on the current song."""
    st = get_state(ctx.guild.id)
    if not st.current:
        return await ctx.send("Nothing is playing. Your vibe: 💀 nonexistent.")
    compliment = random.choice(VIBES)
    embed = discord.Embed(
        title="✨ Vibe Check",
        description=f"**{st.current['title']}**\n\n{compliment}",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

@bot.command(name="8ball")
async def eight_ball(ctx, *, question: str):
    """Ask the magic 8-ball anything."""
    answer = random.choice(EIGHT_BALL)
    embed = discord.Embed(color=discord.Color.dark_purple())
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=f"**{answer}**", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def coinflip(ctx):
    """Flip a coin. The classic decision maker."""
    result = random.choice(["🪙 **Heads!**", "🪙 **Tails!**"])
    await ctx.send(result)

@bot.command()
async def rps(ctx, choice: str):
    """Play Rock Paper Scissors against the bot. Usage: !rps rock"""
    choices = ["rock", "paper", "scissors"]
    emojis  = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    choice  = choice.lower()
    if choice not in choices:
        return await ctx.send("❌ Choose `rock`, `paper`, or `scissors`!")
    bot_choice = random.choice(choices)
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if choice == bot_choice:
        result = "It's a **tie**! 🤝"
    elif wins[choice] == bot_choice:
        result = "You **win**! 🎉 Lucky..."
    else:
        result = "I **win**! 🤖 Skill issue."
    embed = discord.Embed(title="🪨📄✂️ Rock Paper Scissors", color=discord.Color.teal())
    embed.add_field(name="You", value=f"{emojis[choice]} {choice.title()}", inline=True)
    embed.add_field(name="Bot", value=f"{emojis[bot_choice]} {bot_choice.title()}", inline=True)
    embed.add_field(name="Result", value=result, inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def roll(ctx, sides: int = 6):
    """Roll a dice. Default is d6. Usage: !roll 20"""
    if sides < 2 or sides > 1000:
        return await ctx.send("❌ Sides must be between 2 and 1000.")
    result = random.randint(1, sides)
    await ctx.send(f"🎲 You rolled a **d{sides}** and got... **{result}**!")

@bot.command()
async def howdumb(ctx, member: discord.Member = None):
    """Find out how dumb someone is (it's random, don't worry)."""
    target = member or ctx.author
    score  = random.randint(0, 100)
    if score < 20:
        label = "Galaxy-brained genius 🧠"
    elif score < 40:
        label = "Somewhat intelligent 🤔"
    elif score < 60:
        label = "Average human 🙂"
    elif score < 80:
        label = "A little slow 🐢"
    else:
        label = "Certified Goofball 🤡"
    bar = "🟩" * (score // 10) + "⬜" * (10 - score // 10)
    embed = discord.Embed(
        title=f"🧪 Dumbness Test: {target.display_name}",
        description=f"{bar}\n**{score}% dumb** — {label}",
        color=discord.Color.green() if score < 50 else discord.Color.red()
    )
    embed.set_footer(text="Purely scientific. Totally accurate. 100% real.")
    await ctx.send(embed=embed)

@bot.command()
async def simp(ctx, member: discord.Member = None):
    """Check someone's simp level."""
    target = member or ctx.author
    score  = random.randint(0, 100)
    if score < 25:
        label = "Not a simp. Respectable. 😎"
    elif score < 50:
        label = "Mild simp. Keep it together. 😅"
    elif score < 75:
        label = "Certified Simp™ 💘"
    else:
        label = "MAXIMUM SIMP. The council has been notified. 🚨"
    bar = "❤️" * (score // 10) + "🖤" * (10 - score // 10)
    embed = discord.Embed(
        title=f"💘 Simp Meter: {target.display_name}",
        description=f"{bar}\n**{score}% simp** — {label}",
        color=discord.Color.pink() if score > 50 else discord.Color.blurple()
    )
    await ctx.send(embed=embed)

@bot.command()
async def cringe(ctx):
    """Rate the current song's cringe level."""
    st = get_state(ctx.guild.id)
    if not st.current:
        return await ctx.send("Nothing playing. Your silence is 100% cringe though.")
    score = random.randint(0, 100)
    bar   = "😬" * (score // 10) + "😎" * (10 - score // 10)
    embed = discord.Embed(
        title="😬 Cringe Rating",
        description=f"**{st.current['title']}**\n\n{bar}\n**{score}% cringe**",
        color=discord.Color.yellow()
    )
    await ctx.send(embed=embed)

@bot.command()
async def rate(ctx, *, thing: str):
    """Rate literally anything out of 10."""
    score = random.randint(0, 10)
    msgs  = {
        0: "Absolutely terrible. 🗑️",
        1: "Nearly as bad as 0. Yikes.",
        2: "Rough. Very rough.",
        3: "Not great, not terrible... actually terrible.",
        4: "Below average. Disappointing.",
        5: "Exactly average. Boring.",
        6: "Decent, I guess.",
        7: "Pretty good actually! 👍",
        8: "Solid. Respectable.",
        9: "Excellent! Almost perfect.",
        10: "PERFECT. Frame it. 🏆",
    }
    stars = "⭐" * score + "☆" * (10 - score)
    embed = discord.Embed(
        title=f"⭐ Rating: {thing}",
        description=f"{stars}\n**{score}/10** — {msgs[score]}",
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════
#  HELP COMMAND
# ═══════════════════════════════════════════════════════════════

@bot.command(name="help")
async def help_cmd(ctx):
    embed = discord.Embed(title="🎵 GLITCH Music Bot — Commands", color=discord.Color.from_rgb(29, 185, 84))

    music_cmds = [
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
        ("!lyrics `[song]`",      "Get lyrics search link"),
        ("!join / !leave",        "Join or leave voice"),
    ]
    for name, desc in music_cmds:
        embed.add_field(name=name, value=desc, inline=True)

    embed.add_field(
        name="🎛️ Filters",
        value="`none`  `bassboost`  `superbass`  `nightcore`  `vaporwave`  `8d`  `echo`  `karaoke`  `robot`  `underwater`  `treble`  `soft`  `earrape`",
        inline=False
    )

    funny_cmds = [
        ("!roastme",              "Get roasted 🔥"),
        ("!vibe",                 "Vibe check current song"),
        ("!8ball `<question>`",   "Ask the magic 8-ball"),
        ("!coinflip",             "Heads or tails"),
        ("!rps `<r/p/s>`",        "Rock Paper Scissors"),
        ("!roll `[sides]`",       "Roll a dice"),
        ("!howdumb `[@user]`",    "Scientific dumbness test"),
        ("!simp `[@user]`",       "Check simp level"),
        ("!cringe",               "Rate current song's cringe"),
        ("!rate `<anything>`",    "Rate literally anything"),
    ]
    embed.add_field(name="\u200b", value="**🎉 Fun Commands**", inline=False)
    for name, desc in funny_cmds:
        embed.add_field(name=name, value=desc, inline=True)

    embed.set_footer(text="Tip: Use ⏩ Seek button on player card to jump to any position!")
    await ctx.send(embed=embed)

# ═══════════════════════════════════════════════════════════════
#  RUN BOT
# ═══════════════════════════════════════════════════════════════

TOKEN = os.environ.get("TOKEN")
bot.run(TOKEN)
