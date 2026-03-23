import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime
import uuid
import os  # 환경 변수 사용
import calendar

# --- [1] 보안 및 관리자 설정 ---
ADMIN_ID = 1461982946658488411 # jihunqp 전용 ID

def init_db():
    conn = sqlite3.connect("velox_core.db", check_same_thread=False)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS licenses (key TEXT PRIMARY KEY, days INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_verified INTEGER DEFAULT 0, expiry_date DATETIME)")
    cur.execute("CREATE TABLE IF NOT EXISTS attendance (user_id INTEGER PRIMARY KEY, start_time DATETIME, status TEXT DEFAULT 'OFF')")
    cur.execute("CREATE TABLE IF NOT EXISTS work_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, work_date TEXT, seconds INTEGER)")
    conn.commit()
    return conn, cur

db_conn, cursor = init_db()

# --- [2] 권한 체크 데코레이터 ---
def is_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == ADMIN_ID:
            return True
        await interaction.response.send_message("⛔ **jihunqp** 관리자 전용 명령어입니다.", ephemeral=True)
        return False
    return app_commands.check(predicate)

# --- [3] 봇 시스템 코어 ---
class VeloxBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.monitor_msg = None

    async def setup_hook(self):
        self.status_refresh_loop.start()
        self.expiry_check_loop.start()
        await self.tree.sync()
        print(f"✨ [𝙑𝙚𝙡𝙤𝙭𝘾𝙤𝙧𝙚] 시스템 가동 (관리자 ID: {ADMIN_ID})")

    @tasks.loop(seconds=10)
    async def status_refresh_loop(self):
        if self.monitor_msg:
            try:
                new_embed = await build_monitor_embed()
                await self.monitor_msg.edit(embed=new_embed)
            except Exception:
                pass

    @tasks.loop(minutes=5)
    async def expiry_check_loop(self):
        now = datetime.datetime.now()
        cursor.execute("UPDATE users SET is_verified = 0 WHERE expiry_date <= ?", (now,))
        db_conn.commit()

bot = VeloxBot()

# --- [4] UI 컴포넌트 ---
class NeonStatsView(discord.ui.View):
    def __init__(self, user, current_date):
        super().__init__(timeout=60)
        self.user = user
        self.current_date = current_date

    def get_embed(self):
        y, m = self.current_date.year, self.current_date.month
        cursor.execute("SELECT work_date, SUM(seconds) FROM work_logs WHERE user_id = ? AND work_date LIKE ? GROUP BY work_date", 
                       (self.user.id, f"{y}-{m:02d}-%"))
        logs = {row[0]: row[1] for row in cursor.fetchall()}
        cal = calendar.monthcalendar(y, m)
        cal_display = " 일  월  화  수  목  금  토\n"
        for week in cal:
            w_str = ""
            for day in week:
                if day == 0: w_str += " ── "
                else:
                    d_key = f"{y}-{m:02d}-{day:02d}"
                    icon = "💎" if d_key in logs else "▫️"
                    w_str += f"{icon}{day:2d} "
            cal_display += w_str + "\n"
        total_sec = sum(logs.values())
        h, r = divmod(total_sec, 3600)
        m_curr, _ = divmod(r, 60)
        embed = discord.Embed(title=f"🪐 {y}년 {m}월 PERFORMANCE", color=0x00FFFF)
        embed.add_field(name="📅 NEON CALENDAR", value=f"```ml\n{cal_display}```", inline=False)
        embed.add_field(name="⏱️ MONTHLY TOTAL", value=f"총 **{h}시간 {m_curr}분**", inline=True)
        return embed

    @discord.ui.button(label="◀ 이전", style=discord.ButtonStyle.primary)
    async def prev(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=1) - datetime.timedelta(days=1))
        await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="다음 ▶", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=28) + datetime.timedelta(days=5)).replace(day=1)
        await interaction.response.edit_message(embed=self.get_embed())

class VeloxMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def check_license(self, interaction):
        cursor.execute("SELECT is_verified FROM users WHERE user_id = ?", (interaction.user.id,))
        res = cursor.fetchone()
        if res and res[0] == 1: return True
        await interaction.response.send_message("🚫 **라이선스 필요.** 인증된 대원만 버튼을 사용할 수 있습니다.", ephemeral=True)
        return False

    @discord.ui.button(label="🔋 출근", style=discord.ButtonStyle.success)
    async def clock_in(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_license(interaction): return
        cursor.execute("SELECT status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if row and row[0] == 'ON': return await interaction.response.send_message("⚠️ 이미 업무 중입니다.", ephemeral=True)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT OR REPLACE INTO attendance (user_id, start_time, status) VALUES (?, ?, 'ON')", (interaction.user.id, now))
        db_conn.commit()
        await interaction.response.send_message("🔋 **WORK START.**", ephemeral=True)

    @discord.ui.button(label="🏁 퇴근", style=discord.ButtonStyle.danger)
    async def clock_out(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_license(interaction): return
        cursor.execute("SELECT start_time, status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if not row or row[1] == 'OFF': return await interaction.response.send_message("❓ 출근 상태가 아닙니다.", ephemeral=True)
        start_dt = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        sec = int((datetime.datetime.now() - start_dt).total_seconds())
        cursor.execute("INSERT INTO work_logs (user_id, work_date, seconds) VALUES (?, ?, ?)", 
                       (interaction.user.id, datetime.datetime.now().strftime('%Y-%m-%d'), sec))
        cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (interaction.user.id,))
        db_conn.commit()
        await interaction.response.send_message(f"🏁 **WORK END.** `{sec//3600}h {(sec%3600)//60}m`", ephemeral=True)

    @discord.ui.button(label="📊 실적", style=discord.ButtonStyle.secondary)
    async def show_stats(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_license(interaction): return
        v = NeonStatsView(interaction.user, datetime.datetime.now())
        await interaction.response.send_message(embed=v.get_embed(), view=v, ephemeral=True)

# --- [5] 현황판 빌더 ---
async def build_monitor_embed():
    cursor.execute("SELECT user_id, start_time FROM attendance WHERE status = 'ON'")
    members = cursor.fetchall()
    embed = discord.Embed(title="🛰️ VELOXCORE LIVE MONITOR", color=0x00FFFF)
    desc = "🛰️ **[ 실시간 대원 근태 현황 ]**\n━━━━━━━━━━━━━━━━━━━━\n"
    if not members: desc += "❌ 현재 활성화된 대원이 없습니다."
    else:
        for uid, start in members:
            s_dt = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
            elapsed = str(datetime.datetime.now() - s_dt).split('.')[0]
            desc += f"👤 <@{uid}> | `{start[11:16]}` 출근 (`{elapsed}` 경과)\n"
    embed.description = desc
    embed.set_footer(text=f"Last Pulse: {datetime.datetime.now().strftime('%H:%M:%S')}")
    return embed

# --- [6] 명령어 세트 ---
@bot.tree.command(name="생성", description="[관리자] 라이선스 키 생성")
@is_owner()
async def create_license(interaction: discord.Interaction, 기간: int):
    key = f"VX-{uuid.uuid4().hex[:14].upper()}"
    cursor.execute("INSERT INTO licenses (key, days) VALUES (?, ?)", (key, 기간))
    db_conn.commit()
    await interaction.response.send_message(f"🔑 **라이선스 생성 완료:** `{key}` ({기간}일권)", ephemeral=True)

@bot.tree.command(name="메뉴", description="관리자용 메뉴 호출")
@is_owner()
async def menu_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📋 𝙑𝙚𝙡𝙤𝙭𝘾𝙤𝙧𝙚 𝙈𝙖𝙣𝙖𝙜𝙚𝙢𝙚𝙣𝙩", color=0xFF00FF)
    await interaction.response.send_message(embed=embed, view=VeloxMenuView())

@bot.tree.command(name="현황", description="현황판 소환")
@is_owner()
async def monitor_cmd(interaction: discord.Interaction):
    embed = await build_monitor_embed()
    await interaction.response.send_message(embed=embed)
    bot.monitor_msg = await interaction.original_response()

@bot.tree.command(name="인증", description="라이선스 키 등록")
async def verify_cmd(interaction: discord.Interaction, 키: str):
    cursor.execute("SELECT days FROM licenses WHERE key = ?", (키,))
    res = cursor.fetchone()
    if not res: return await interaction.response.send_message("❌ 이미 사용되었거나 없는 키입니다.", ephemeral=True)
    expiry = datetime.datetime.now() + datetime.timedelta(days=res[0])
    cursor.execute("INSERT OR REPLACE INTO users (user_id, is_verified, expiry_date) VALUES (?, 1, ?)", (interaction.user.id, expiry))
    cursor.execute("DELETE FROM licenses WHERE key = ?", (키,))
    db_conn.commit()
    await interaction.response.send_message(f"✅ 인증 완료! 만료일: {expiry.date()}", ephemeral=True)

# --- [7] 실행 ---
token = os.getenv("DISCORD_TOKEN")
bot.run(token)
