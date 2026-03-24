import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime
import uuid
import os
import calendar
import pytz
from flask import Flask
from threading import Thread

# --- [0] 환경 및 보안 설정 ---
KST = pytz.timezone('Asia/Seoul')
ADMIN_ID = 1461982946658488411  # jihunqp 전용

app = Flask('')
@app.route('/')
def home(): return "🛰️ VELOXCORE TACTICAL OS ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- [1] 데이터베이스 (영구 저장 강화) ---
def init_db():
    # 파일 이름을 고정하여 재시작 시에도 해당 파일을 읽도록 설정
    conn = sqlite3.connect("velox_ultimate.db", check_same_thread=False)
    cur = conn.cursor()
    # 라이선스 키 저장소
    cur.execute("CREATE TABLE IF NOT EXISTS licenses (key TEXT PRIMARY KEY, days INTEGER)")
    # 유저 인증 및 만료 정보
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_verified INTEGER DEFAULT 0, expiry_date TEXT)")
    # 현재 가동 상태 (세션)
    cur.execute("CREATE TABLE IF NOT EXISTS attendance (user_id INTEGER PRIMARY KEY, start_time TEXT, status TEXT DEFAULT 'OFF')")
    # 누적 작업 로그
    cur.execute("CREATE TABLE IF NOT EXISTS work_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, work_date TEXT, seconds INTEGER)")
    conn.commit()
    return conn, cur

db_conn, cursor = init_db()

def get_now(): return datetime.datetime.now(KST)

# --- [2] UI 컴포넌트 ---
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
        
        cal_display = "  SUN  MON  TUE  WED  THU  FRI  SAT\n"
        for week in cal:
            w_str = ""
            for day in week:
                if day == 0: w_str += "  ── "
                else:
                    d_key = f"{y}-{m:02d}-{day:02d}"
                    icon = "💎" if d_key in logs else "▫️"
                    w_str += f"{icon}{day:2d} "
            cal_display += w_str + "\n"
            
        total_sec = sum(logs.values())
        h, r2 = divmod(total_sec, 3600)
        m_curr, s_curr = divmod(r2, 60)
        
        embed = discord.Embed(title=f"🪐 {y}/{m} PROTOCOL ANALYTICS", color=0x00FFFF)
        embed.description = f"```ml\n{cal_display}```"
        embed.add_field(name="🛰️ CUMULATIVE UPTIME", 
                        value=f"**`{h}시간 {m_curr}분 {s_curr}초`**", inline=False)
        return embed

    @discord.ui.button(label="PREV", style=discord.ButtonStyle.secondary, emoji="⬅️")
    async def prev(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=1) - datetime.timedelta(days=1))
        await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="NEXT", style=discord.ButtonStyle.secondary, emoji="➡️")
    async def next(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=28) + datetime.timedelta(days=5)).replace(day=1)
        await interaction.response.edit_message(embed=self.get_embed())

class VeloxMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def check_auth(self, interaction: discord.Interaction):
        cursor.execute("SELECT is_verified, expiry_date FROM users WHERE user_id = ?", (interaction.user.id,))
        res = cursor.fetchone()
        if not res or res[0] == 0:
            await interaction.response.send_message("❌ **ACCESS DENIED: 미인증 유저**", ephemeral=True)
            return False
        expiry = datetime.datetime.strptime(res[1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        if get_now() > expiry:
            await interaction.response.send_message("🚫 **LICENSE EXPIRED: 라이선스 만료**", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="SYSTEM START", style=discord.ButtonStyle.success, emoji="🔋")
    async def clock_in(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        cursor.execute("SELECT status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if row and row[0] == 'ON': return await interaction.response.send_message("⚠️ 이미 가동 중입니다.", ephemeral=True)
        
        now_str = get_now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT OR REPLACE INTO attendance (user_id, start_time, status) VALUES (?, ?, 'ON')", (interaction.user.id, now_str))
        db_conn.commit() # 즉시 저장
        await interaction.response.send_message("🔋 **OPERATIONAL.** 가동 세션을 시작합니다.", ephemeral=True)

    @discord.ui.button(label="TERMINATE", style=discord.ButtonStyle.danger, emoji="🏁")
    async def clock_out(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        cursor.execute("SELECT start_time, status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if not row or row[1] == 'OFF': return await interaction.response.send_message("❓ 활성 세션이 없습니다.", ephemeral=True)
        
        start_dt = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        sec = int((get_now() - start_dt).total_seconds())
        cursor.execute("INSERT INTO work_logs (user_id, work_date, seconds) VALUES (?, ?, ?)", 
                       (interaction.user.id, get_now().strftime('%Y-%m-%d'), sec))
        cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (interaction.user.id,))
        db_conn.commit() # 즉시 저장
        await interaction.response.send_message(f"🏁 **TERMINATED.** 가동 시간: `{sec//3600}시간 {(sec%3600)//60}분 {sec%60}초`", ephemeral=True)

    @discord.ui.button(label="ANALYTICS", style=discord.ButtonStyle.primary, emoji="📊")
    async def show_stats(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        v = NeonStatsView(interaction.user, get_now())
        await interaction.response.send_message(embed=v.get_embed(), view=v, ephemeral=True)

# --- [3] 봇 코어 및 실시간 현황판 ---
class VeloxBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
        self.monitor_msg = None

    async def setup_hook(self):
        self.status_refresh_loop.start()
        await self.tree.sync()
        print("✨ VELOXCORE TACTICAL READY")

    @tasks.loop(seconds=10)
    async def status_refresh_loop(self):
        if self.monitor_msg:
            try:
                cursor.execute("SELECT user_id, start_time FROM attendance WHERE status = 'ON'")
                members = cursor.fetchall()
                embed = discord.Embed(title="📡 LIVE TACTICAL MONITORING", color=0x00FFFF)
                desc = "🛰️ **[ ACTIVE OPERATIONAL NODES ]**\n" + "━" * 28 + "\n"
                
                if not members:
                    desc += "❌ 현재 활성화된 세션이 없습니다."
                else:
                    for uid, start in members:
                        s_dt = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
                        total_seconds = int((get_now() - s_dt).total_seconds())
                        if total_seconds < 0: total_seconds = 0
                        hours, rem = divmod(total_seconds, 3600)
                        minutes, seconds = divmod(rem, 60)
                        desc += f"👤 <@{uid}>\n┗ ⚡ **UPTIME:** `{hours}시간 {minutes}분 {seconds}초`\n"
                
                embed.description = desc
                embed.set_footer(text=f"PULSE: {get_now().strftime('%Y-%m-%d %H:%M:%S')} KST")
                await self.monitor_msg.edit(embed=embed)
            except Exception: pass

bot = VeloxBot()

# --- [4] 관리자 전용 명령어 ---
def is_admin():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == ADMIN_ID: return True
        await interaction.response.send_message("🚫 **접근 거부: 관리자 전용입니다.**", ephemeral=True)
        return False
    return app_commands.check(predicate)

@bot.tree.command(name="생성", description="[ADMIN] 보안 라이선스 생성")
@is_admin()
async def create_license(interaction: discord.Interaction, 기간: int):
    key = f"VX-{uuid.uuid4().hex[:14].upper()}"
    cursor.execute("INSERT INTO licenses (key, days) VALUES (?, ?)", (key, 기간))
    db_conn.commit() # 즉시 저장
    embed = discord.Embed(title="🔑 LICENSE GENERATED", color=0xFFFF00)
    embed.add_field(name="KEY", value=f"```css\n{key}```", inline=False)
    embed.add_field(name="VALIDITY", value=f"`{기간}` Days", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="현황", description="[ADMIN] 실시간 현황판 소환")
@is_admin()
async def monitor_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📡 MONITORING INITIALIZING...", color=0x00FFFF)
    await interaction.response.send_message(embed=embed)
    bot.monitor_msg = await interaction.original_response()

@bot.tree.command(name="강제종료", description="[ADMIN] 특정 유저 세션 킬")
@is_admin()
async def force_stop(interaction: discord.Interaction, 유저: discord.Member):
    cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (유저.id,))
    db_conn.commit()
    await interaction.response.send_message(f"🚨 <@{유저.id}> 세션이 강제 종료되었습니다.", ephemeral=True)

# --- [5] 공용 명령어 ---
@bot.tree.command(name="메뉴", description="가동 컨트롤 센터 (인증자 전용)")
async def menu_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="⚡ VELOXCORE TACTICAL CENTER", color=0xFF00FF)
    embed.description = "시스템 가동 상태 및 실적 로그를 관리합니다."
    await interaction.response.send_message(embed=embed, view=VeloxMenuView())

@bot.tree.command(name="인증", description="라이선스 키 등록")
async def verify_cmd(interaction: discord.Interaction, 키: str):
    cursor.execute("SELECT days FROM licenses WHERE key = ?", (키,))
    res = cursor.fetchone()
    if not res: return await interaction.response.send_message("❌ 유효하지 않은 키입니다.", ephemeral=True)
    
    expiry = get_now() + datetime.timedelta(days=res[0])
    expiry_str = expiry.strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("INSERT OR REPLACE INTO users (user_id, is_verified, expiry_date) VALUES (?, 1, ?)", (interaction.user.id, expiry_str))
    cursor.execute("DELETE FROM licenses WHERE key = ?", (키,))
    db_conn.commit() # 데이터베이스 파일에 즉시 영구 저장
    
    embed = discord.Embed(title="✅ AUTHENTICATION COMPLETE", color=0x00FF00)
    embed.description = f"시스템 권한이 승인되었습니다.\n**만료일:** `{expiry_str}`"
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- [6] 실행 ---
if __name__ == "__main__":
    Thread(target=run_web).start()
    token = os.getenv("DISCORD_TOKEN")
    if token: bot.run(token)
    else: print("❌ TOKEN NOT FOUND")
