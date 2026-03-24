import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime
import uuid
import os
import calendar
import pytz  # 한국 시간 설정을 위해 필요
from flask import Flask
from threading import Thread

# --- [0] 시스템 환경 설정 ---
KST = pytz.timezone('Asia/Seoul')
ADMIN_ID = 1461982946658488411  # jihunqp 전용 보안 식별자

app = Flask('')
@app.route('/')
def home():
    return "🛰️ VELOXCORE TACTICAL OS IS ONLINE"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- [1] 데이터베이스 보안 레이어 ---
def init_db():
    conn = sqlite3.connect("velox_tactical.db", check_same_thread=False)
    cur = conn.cursor()
    # 라이선스, 유저 정보, 근태 기록, 로그 테이블 보안 구성
    cur.execute("CREATE TABLE IF NOT EXISTS licenses (key TEXT PRIMARY KEY, days INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_verified INTEGER DEFAULT 0, expiry_date DATETIME)")
    cur.execute("CREATE TABLE IF NOT EXISTS attendance (user_id INTEGER PRIMARY KEY, start_time DATETIME, status TEXT DEFAULT 'OFF')")
    cur.execute("CREATE TABLE IF NOT EXISTS work_logs (log_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, work_date TEXT, seconds INTEGER)")
    conn.commit()
    return conn, cur

db_conn, cursor = init_db()

# --- [2] 유틸리티: KST 현재 시간 획득 ---
def get_now():
    return datetime.datetime.now(KST)

# --- [3] 하이엔드 UI 컴포넌트 ---
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
        
        cal_display = "  일   월   화   수   목   금   토\n"
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
        h, r = divmod(total_sec, 3600)
        m_curr, s_curr = divmod(r, 60)
        
        embed = discord.Embed(title=f"🪐 {y} / {m} NEON PROTOCOL", color=0x00FFFF)
        embed.description = f"```ml\n{cal_display}```"
        embed.add_field(name="🛰️ CUMULATIVE UPTIME", value=f"**{h}**H **{m_curr}**M **{s_curr}**S", inline=True)
        embed.set_footer(text="CORE ANALYTICS SYSTEM v2.0")
        return embed

    @discord.ui.button(label="⋘ 이전 달", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=1) - datetime.timedelta(days=1))
        await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="다음 달 ⋙", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=28) + datetime.timedelta(days=5)).replace(day=1)
        await interaction.response.edit_message(embed=self.get_embed())

class VeloxMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def check_auth(self, interaction: discord.Interaction):
        # 라이선스 인증 여부 및 만료일 체크 보안 로직
        cursor.execute("SELECT is_verified, expiry_date FROM users WHERE user_id = ?", (interaction.user.id,))
        res = cursor.fetchone()
        if not res or res[0] == 0:
            await interaction.response.send_message("❌ **접근 거부: 라이선스 미인증 사용자.**", ephemeral=True)
            return False
        
        # 만료일 체크 (KST 기준)
        expiry = datetime.datetime.strptime(res[1], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC).astimezone(KST)
        if get_now() > expiry:
            await interaction.response.send_message("🚫 **라이선스가 만료되었습니다. 관리자에게 문의하세요.**", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="SYSTEM START", style=discord.ButtonStyle.success, emoji="🔋")
    async def clock_in(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        
        cursor.execute("SELECT status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if row and row[0] == 'ON': return await interaction.response.send_message("⚠️ 시스템이 이미 가동 중입니다.", ephemeral=True)
        
        now_str = get_now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT OR REPLACE INTO attendance (user_id, start_time, status) VALUES (?, ?, 'ON')", (interaction.user.id, now_str))
        db_conn.commit()
        await interaction.response.send_message(f"🔋 **OPERATIONAL.** 가동 시작 시각: `{now_str}`", ephemeral=True)

    @discord.ui.button(label="TERMINATE", style=discord.ButtonStyle.danger, emoji="🏁")
    async def clock_out(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        
        cursor.execute("SELECT start_time, status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if not row or row[1] == 'OFF': return await interaction.response.send_message("❓ 가동 중인 세션이 없습니다.", ephemeral=True)
        
        # 가동 시간 계산
        start_dt = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        sec = int((get_now() - start_dt).total_seconds())
        
        cursor.execute("INSERT INTO work_logs (user_id, work_date, seconds) VALUES (?, ?, ?)", 
                       (interaction.user.id, get_now().strftime('%Y-%m-%d'), sec))
        cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (interaction.user.id,))
        db_conn.commit()
        await interaction.response.send_message(f"🏁 **TERMINATED.** 누적 가동 시간: `{sec//3600}시간 {(sec%3600)//60}분 {sec%60}초`", ephemeral=True)

    @discord.ui.button(label="MY ANALYTICS", style=discord.ButtonStyle.primary, emoji="📊")
    async def show_stats(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not await self.check_auth(interaction): return
        v = NeonStatsView(interaction.user, get_now())
        await interaction.response.send_message(embed=v.get_embed(), view=v, ephemeral=True)

# --- [4] 봇 코어 시스템 ---
class VeloxBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.all())
        self.monitor_msg = None

    async def setup_hook(self):
        self.status_refresh_loop.start()
        await self.tree.sync()
        print(f"✨ [𝙑𝙚𝙡𝙤𝙭𝘾𝙤𝙧𝙚 𝙏𝙖𝙘𝙩𝙞𝙘𝙖𝙡] 시스템 가동 완료")

    @tasks.loop(seconds=10) # 10초마다 실시간 변동 적용
    async def status_refresh_loop(self):
        if self.monitor_msg:
            try:
                cursor.execute("SELECT user_id, start_time FROM attendance WHERE status = 'ON'")
                members = cursor.fetchall()
                embed = discord.Embed(title="📡 LIVE TACTICAL MONITORING", color=0x00FFFF)
                desc = "🛰️ **[ ACTIVE OPERATORS ]**\n" + "━" * 25 + "\n"
                
                if not members:
                    desc += "❌ 활성화된 유저가 없습니다."
                else:
                    for uid, start in members:
                        s_dt = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
                        elapsed = str(get_now() - s_dt).split('.')[0]
                        desc += f"👤 <@{uid}> | `{start[11:19]}` 출근 | **`{elapsed}`** 경과\n"
                
                embed.description = desc
                embed.set_footer(text=f"LAST PULSE: {get_now().strftime('%Y-%m-%d %H:%M:%S')} KST")
                await self.monitor_msg.edit(embed=embed)
            except Exception as e:
                print(f"Loop Error: {e}")

bot = VeloxBot()

# --- [5] 전술 명령어 세트 (jihunqp 전용) ---
def is_admin():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == ADMIN_ID: return True
        await interaction.response.send_message("🚫 **접근 거부: 관리자 권한이 필요합니다.**", ephemeral=True)
        return False
    return app_commands.check(predicate)

@bot.tree.command(name="생성", description="[ADMIN] 보안 라이선스 키 생성")
@is_admin()
async def create_license(interaction: discord.Interaction, 기간: int):
    key = f"VX-{uuid.uuid4().hex[:14].upper()}"
    cursor.execute("INSERT INTO licenses (key, days) VALUES (?, ?)", (key, 기간))
    db_conn.commit()
    await interaction.response.send_message(f"🔑 **ENCRYPTED KEY:** `{key}`\n유효 기간: `{기간}`일", ephemeral=True)

@bot.tree.command(name="현황", description="[ADMIN] 실시간 현황 모니터링 패널 고정")
@is_admin()
async def monitor_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="📡 LIVE TACTICAL MONITORING", description="데이터 동기화 시퀀스 개시...", color=0x00FFFF)
    await interaction.response.send_message(embed=embed)
    bot.monitor_msg = await interaction.original_response()

@bot.tree.command(name="강제종료", description="[ADMIN] 특정 유저의 가동 세션 강제 종료")
@is_admin()
async def force_stop(interaction: discord.Interaction, 유저: discord.Member):
    cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (유저.id,))
    db_conn.commit()
    await interaction.response.send_message(f"🚨 <@{유저.id}> 유저의 세션을 강제 종료했습니다.", ephemeral=True)

# --- [6] 공용 명령어 ---
@bot.tree.command(name="메뉴", description="가동 컨트롤 패널 호출 (인증자 전용)")
async def menu_cmd(interaction: discord.Interaction):
    # 메뉴 호출 시점에서 라이선스 체크
    cursor.execute("SELECT is_verified FROM users WHERE user_id = ?", (interaction.user.id,))
    res = cursor.fetchone()
    if not res or res[0] == 0:
        return await interaction.response.send_message("❌ **인증되지 않은 사용자입니다. `/인증` 명령어를 먼저 사용하세요.**", ephemeral=True)
    
    embed = discord.Embed(title="⚡ VELOXCORE OPERATION UNIT", color=0xFF00FF)
    embed.description = "시스템 가동 상태를 관리하고 실적 분석 로그에 접근합니다."
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=VeloxMenuView())

@bot.tree.command(name="인증", description="라이선스 키를 사용하여 시스템 권한 획득")
async def verify_cmd(interaction: discord.Interaction, 키: str):
    cursor.execute("SELECT days FROM licenses WHERE key = ?", (키,))
    res = cursor.fetchone()
    if not res: return await interaction.response.send_message("❌ 유효하지 않거나 이미 사용된 키입니다.", ephemeral=True)
    
    # KST 기준 만료일 계산
    expiry = get_now() + datetime.timedelta(days=res[0])
    expiry_str = expiry.strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute("INSERT OR REPLACE INTO users (user_id, is_verified, expiry_date) VALUES (?, 1, ?)", (interaction.user.id, expiry_str))
    cursor.execute("DELETE FROM licenses WHERE key = ?", (키,))
    db_conn.commit()
    await interaction.response.send_message(f"✅ **인증 성공.**\n만료 일시: `{expiry_str}` KST", ephemeral=True)

# --- [7] 시스템 런처 ---
if __name__ == "__main__":
    # 라이브러리 설치 확인용 (pytz 추가 필수)
    Thread(target=run_web).start()
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ CRITICAL ERROR: TOKEN NOT FOUND")
