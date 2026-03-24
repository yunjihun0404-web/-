import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import datetime
import uuid
import os
import calendar
from flask import Flask
from threading import Thread

# --- [0] Render 포트 자동 바인딩 및 생존 서버 ---
app = Flask('')

@app.route('/')
def home():
    return "🛰️ VeloxCore System is Active and Monitoring."

def run_web():
    # Render가 주는 PORT 환경변수를 우선 사용, 없으면 8080 사용
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- [1] 데이터베이스 초기화 ---
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
ADMIN_ID = 1461982946658488411 # jihunqp 전용

# --- [2] 프리미엄 UI 컴포넌트 ---
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
        m_curr, _ = divmod(r, 60)
        
        embed = discord.Embed(title=f"🪐 {y} / {m} NEON PERFORMANCE", color=0x00FFFF)
        embed.description = f"```ml\n{cal_display}```"
        embed.add_field(name="🛰️ TOTAL TIME", value=f"**{h}** HOURS **{m_curr}** MINS", inline=True)
        embed.set_image(url="https://i.imgur.com/B9O0W3L.png") # 네온 라인 장식 (예시)
        return embed

    @discord.ui.button(label="⋘ PREV", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=1) - datetime.timedelta(days=1))
        await interaction.response.edit_message(embed=self.get_embed())

    @discord.ui.button(label="NEXT ⋙", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, btn: discord.ui.Button):
        self.current_date = (self.current_date.replace(day=28) + datetime.timedelta(days=5)).replace(day=1)
        await interaction.response.edit_message(embed=self.get_embed())

class VeloxMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="POWER ON", style=discord.ButtonStyle.success, emoji="🔋")
    async def clock_in(self, interaction: discord.Interaction, btn: discord.ui.Button):
        cursor.execute("SELECT is_verified FROM users WHERE user_id = ?", (interaction.user.id,))
        res = cursor.fetchone()
        if not res or res[0] == 0: return await interaction.response.send_message("❌ **라이선스가 만료되었습니다.**", ephemeral=True)
        
        cursor.execute("SELECT status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if row and row[0] == 'ON': return await interaction.response.send_message("⚠️ 이미 가동 중입니다.", ephemeral=True)
        
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute("INSERT OR REPLACE INTO attendance (user_id, start_time, status) VALUES (?, ?, 'ON')", (interaction.user.id, now))
        db_conn.commit()
        await interaction.response.send_message("🔋 **OPERATIONAL.** 시스템 가동을 시작합니다.", ephemeral=True)

    @discord.ui.button(label="TERMINATE", style=discord.ButtonStyle.danger, emoji="🏁")
    async def clock_out(self, interaction: discord.Interaction, btn: discord.ui.Button):
        cursor.execute("SELECT start_time, status FROM attendance WHERE user_id = ?", (interaction.user.id,))
        row = cursor.fetchone()
        if not row or row[1] == 'OFF': return await interaction.response.send_message("❓ 가동 상태가 아닙니다.", ephemeral=True)
        
        start_dt = datetime.datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        sec = int((datetime.datetime.now() - start_dt).total_seconds())
        cursor.execute("INSERT INTO work_logs (user_id, work_date, seconds) VALUES (?, ?, ?)", 
                       (interaction.user.id, datetime.datetime.now().strftime('%Y-%m-%d'), sec))
        cursor.execute("UPDATE attendance SET status = 'OFF' WHERE user_id = ?", (interaction.user.id,))
        db_conn.commit()
        await interaction.response.send_message(f"🏁 **TERMINATED.** 누적 가동: `{sec//3600}h {(sec%3600)//60}m`", ephemeral=True)

    @discord.ui.button(label="ANALYTICS", style=discord.ButtonStyle.primary, emoji="📊")
    async def show_stats(self, interaction: discord.Interaction, btn: discord.ui.Button):
        v = NeonStatsView(interaction.user, datetime.datetime.now())
        await interaction.response.send_message(embed=v.get_embed(), view=v, ephemeral=True)

# --- [3] 봇 코어 시스템 ---
class VeloxBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.monitor_msg = None

    async def setup_hook(self):
        self.status_refresh_loop.start()
        await self.tree.sync()
        print(f"✨ [𝙑𝙚𝙡𝙤𝙭𝘾𝙤𝙧𝙚] 인스턴스 활성화 완료")

    @tasks.loop(seconds=15)
    async def status_refresh_loop(self):
        if self.monitor_msg:
            try:
                cursor.execute("SELECT user_id, start_time FROM attendance WHERE status = 'ON'")
                members = cursor.fetchall()
                embed = discord.Embed(title="📡 LIVE VELOX MONITORING", color=0x00FFFF)
                desc = "🛰️ **[ ACTIVE OPERATORS ]**\n" + "━" * 20 + "\n"
                if not members: desc += "❌ 현재 활성화된 세션이 없습니다."
                else:
                    for uid, start in members:
                        s_dt = datetime.datetime.strptime(start, '%Y-%m-%d %H:%M:%S')
                        elapsed = str(datetime.datetime.now() - s_dt).split('.')[0]
                        desc += f"🆔 <@{uid}> | `{start[11:16]}` 출근 | `{elapsed}`\n"
                embed.description = desc
                embed.set_footer(text=f"PULSE: {datetime.datetime.now().strftime('%H:%M:%S')}")
                await self.monitor_msg.edit(embed=embed)
            except Exception: pass

bot = VeloxBot()

# --- [4] 슬래시 명령어 세트 ---
@bot.tree.command(name="생성", description="[ADMIN] 라이선스 암호 생성")
async def create_license(interaction: discord.Interaction, 기간: int):
    if interaction.user.id != ADMIN_ID: return await interaction.response.send_message("⛔ 권한 거부.", ephemeral=True)
    key = f"VX-{uuid.uuid4().hex[:12].upper()}"
    cursor.execute("INSERT INTO licenses (key, days) VALUES (?, ?)", (key, 기간))
    db_conn.commit()
    await interaction.response.send_message(f"🔑 **ENCRYPTED KEY:** `{key}` ({기간}일)", ephemeral=True)

@bot.tree.command(name="메뉴", description="가동 컨트롤 패널 호출")
async def menu_cmd(interaction: discord.Interaction):
    embed = discord.Embed(title="⚡ VELOXCORE OPERATION UNIT", color=0xFF00FF)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.description = "시스템 가동, 종료 및 실적 분석을 제어합니다."
    await interaction.response.send_message(embed=embed, view=VeloxMenuView())

@bot.tree.command(name="현황", description="실시간 현황판 고정")
async def monitor_cmd(interaction: discord.Interaction):
    if interaction.user.id != ADMIN_ID: return await interaction.response.send_message("⛔ 권한 거부.", ephemeral=True)
    embed = discord.Embed(title="📡 LIVE VELOX MONITORING", description="데이터 동기화 중...", color=0x00FFFF)
    await interaction.response.send_message(embed=embed)
    bot.monitor_msg = await interaction.original_response()

@bot.tree.command(name="인증", description="라이선스 키 데이터 등록")
async def verify_cmd(interaction: discord.Interaction, 키: str):
    cursor.execute("SELECT days FROM licenses WHERE key = ?", (키,))
    res = cursor.fetchone()
    if not res: return await interaction.response.send_message("❌ 유효하지 않은 키입니다.", ephemeral=True)
    expiry = datetime.datetime.now() + datetime.timedelta(days=res[0])
    cursor.execute("INSERT OR REPLACE INTO users (user_id, is_verified, expiry_date) VALUES (?, 1, ?)", (interaction.user.id, expiry))
    cursor.execute("DELETE FROM licenses WHERE key = ?", (키,))
    db_conn.commit()
    await interaction.response.send_message(f"✅ **인증 성공.** 시스템 사용 권한이 부여되었습니다.", ephemeral=True)

# --- [5] 시스템 가동 ---
if __name__ == "__main__":
    Thread(target=run_web).start() # 웹 서버 동시 실행
    token = os.getenv("DISCORD_TOKEN")
    if token:
        bot.run(token)
    else:
        print("❌ DISCORD_TOKEN을 찾을 수 없습니다.")
