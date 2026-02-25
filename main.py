import os
import re
import requests
import discord
from discord.ext import commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Thread
from flask import Flask
import time

# --- 1. Webサーバー設定 ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 2. 設定 & ヘッダー ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}

# --- 3. 【重要】process_url 関数 (ここが欠けていたためエラーでした) ---
def scrape_amazon(url):
    try:
        time.sleep(1)
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code != 200: return None
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title = soup.find(id='productTitle')
        title = title.get_text().strip() if title else "Amazon Product"
        
        price = soup.select_one('.a-price .a-offscreen')
        price = price.get_text().strip() if price else "N/A"
        
        img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
        img_url = img.get('src') if img and not img.get('content') else (img.get('content') if img else "")
        
        asin_m = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
        asin = asin_m.group(1) if asin_m else None
        
        return title[:60], price, img_url, asin
    except:
        return None

def process_url(url, author):
    data = scrape_amazon(url)
    if not data: return None
    title, price, img, asin = data
    
    domain = "amazon.co.jp"
    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    embed.add_field(name="価格", value=price, inline=True)
    embed.set_thumbnail(url=img)
    embed.set_footer(text=f"Shared by {author.display_name}")
    return embed

# --- 4. メインロジック (on_message) ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready(): print(f'✅ {bot.user} 起動完了')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    urls = re.findall(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', message.content)
    if not urls: return

    target_url = urls[0]
    user_comment = re.sub(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', '', message.content).strip()
    
    # ロード中メッセージ
    status_msg = await message.channel.send("⌛ **リンクを確認中...**")

    # Amazon判定
    if "amazon." in target_url or "amzn." in target_url:
        embed = process_url(target_url, message.author)
        if embed:
            if user_comment: embed.description = f"**コメント:**\n{user_comment}"
            await message.delete()
            await status_msg.edit(content=None, embed=embed)
            return

    # 80文字以上の汎用短縮
    if len(target_url) > 80:
        domain = re.search(r'https?://([^/]+)', target_url).group(1)
        desc = f"[{domain} へ移動]({target_url})"
        if user_comment: desc = f"**コメント:** {user_comment}\n\n" + desc
        
        short_embed = discord.Embed(title="🔗 URLを整理しました", description=desc, color=0xcccccc)
        await message.delete()
        await status_msg.edit(content=None, embed=short_embed)
    else:
        await status_msg.delete()

# --- 5. 実行 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)