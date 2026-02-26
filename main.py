import os
import re
import asyncio
import aiohttp
import discord
from discord.ext import commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Thread
from flask import Flask
from cachetools import TTLCache

# --- 1. Webサーバー設定 (Keep Alive用) ---
app = Flask('')
@app.route('/')
def home(): return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()

# --- 2. 設定と環境変数 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'your-tag-22')

# ブラウザ偽装ヘッダー
BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'ja-JP,ja;q=0.9,en;q=0.8'
}

# 簡易メモリキャッシュ (最大100件、有効期限5分)
url_cache = TTLCache(maxsize=100, ttl=300)
# 同時実行制限 (サーバー負荷とDiscord API制限の考慮)
sem = asyncio.Semaphore(5)

# 多言語設定
LOCALE_SETTINGS = {
    "amazon.co.jp": {"lang": "ja", "comment": "コメント", "shared": "投稿者"},
    "amazon.com": {"lang": "en", "comment": "Comment", "shared": "Shared by"},
    "amazon.co.uk": {"lang": "en", "comment": "Comment", "shared": "Shared by"}
}
DEFAULT_LOCALE = {"lang": "en", "comment": "Comment", "shared": "Shared by"}

# --- 3. 処理関数 (非同期) ---

def safe_truncate(text, limit):
    return text[:limit] if text else ""

async def fetch_url_data(session, url):
    """URLからOGPデータを取得（リダイレクトを追跡）"""
    if url in url_cache:
        return url_cache[url]
    
    try:
        # allow_redirects=True で amzn.to 等を解決
        async with session.get(url, headers=BASE_HEADERS, timeout=10, allow_redirects=True) as res:
            final_url = str(res.url)
            html = await res.text()
            soup = BeautifulSoup(html, 'html.parser')
            
            title_tag = soup.find("meta", property="og:title") or soup.find("title")
            img_tag = soup.find("meta", property="og:image")
            
            title = title_tag["content"] if title_tag and title_tag.has_attr("content") else (title_tag.text if title_tag else "Link")
            img_url = img_tag["content"] if img_tag else None
            
            result = (safe_truncate(title.strip(), 200), img_url, final_url)
            url_cache[url] = result
            return result
    except Exception as e:
        print(f"[Fetch Error] {url}: {e}")
        return "Link", None, url

def extract_asin(url):
    """URLからASINを抽出"""
    match = re.search(r'/([A-Z0-9]{10})(?:[/?]|$)', url)
    return match.group(1) if match else None

async def build_custom_embed(session, url, author, comment):
    """URLの種類に応じたEmbedを構築"""
    title, img, final_url = await fetch_url_data(session, url)
    
    # Amazon判定 (amzn.to展開後も含む)
    if any(d in final_url.lower() for d in ["amazon.", "amzn."]):
        asin = extract_asin(final_url)
        if asin:
            domain_match = re.search(r'https?://([^/]+)', final_url)
            domain = domain_match.group(1) if domain_match else "amazon.co.jp"
            config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
            
            tagged_url = f"https://{domain}/dp/{asin}?tag={AMAZON_TAG}"
            embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
            if comment:
                embed.description = f"**{config['comment']}:**\n{safe_truncate(comment, 500)}"
            if img:
                embed.set_thumbnail(url=img)
            embed.set_footer(text=f"{config['shared']} {author.display_name} | {domain}")
            return embed

    # 一般サイト
    embed = discord.Embed(title=title, url=final_url.split('?')[0], color=0xcccccc)
    if comment:
        embed.description = f"**Comment:**\n{safe_truncate(comment, 500)}"
    if img:
        embed.set_thumbnail(url=img)
    domain_label = re.search(r'https?://([^/]+)', final_url)
    embed.set_footer(text=f"Shared by {author.display_name} | {domain_label.group(1) if domain_label else ''}")
    return embed

# --- 4. Botロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    if not hasattr(bot, 'session'):
        bot.session = aiohttp.ClientSession()
    print(f'✅ {bot.user} Online (Async Integrated Mode)')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    # 除外リスト
    exclude_domains = [
        "youtube.com", "youtu.be", "twitter.com", "x.com",
        "instagram.com", "tiktok.com", "facebook.com",
        "spotify.com", "apple.com", "twitch.tv", "nicovideo.jp",
        "pixiv.net", "note.com", "discord.com"
    ]
    if any(domain in found_urls[0].lower() for domain in exclude_domains):
        return

    # 処理開始 (セマフォで並行数制限)
    async with sem:
        status_msg = await message.channel.send("⌛ **Processing...**")
        
        clean_comment = message.content
        for u in found_urls:
            clean_comment = clean_comment.replace(u, "")
        clean_comment = clean_comment.strip()

        try:
            embed = await build_custom_embed(bot.session, found_urls[0], message.author, clean_comment)
            
            if embed:
                # 削除権限がない場合でも止まらないように
                try:
                    await message.delete()
                except:
                    pass
                await status_msg.edit(content=None, embed=embed)
            else:
                await status_msg.delete()

        except Exception as e:
            print(f"[Error] {e}")
            try: await status_msg.delete()
            except: pass

# --- 5. 実行 ---
if __name__ == "__main__":
    keep_alive()
    try:
        bot.run(TOKEN)
    finally:
        # セッションのクリーンアップ
        async def close_session():
            if hasattr(bot, 'session'):
                await bot.session.close()
        try:
            asyncio.run(close_session())
        except: pass