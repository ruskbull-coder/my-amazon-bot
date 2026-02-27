import os
import re
import requests
import discord
from discord.ext import commands
from discord.ui import Button, View 
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Thread
from flask import Flask
import asyncio

# --- 1. Webサーバー設定 (Render常時稼働用) ---
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

# --- 2. 設定と環境変数 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

BASE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}

LOCALE_SETTINGS = {
    "amazon.co.jp": {
        "accept_lang": "ja-JP,ja;q=0.9",
        "comment": "コメント", "shared": "投稿者"
    },
    "amazon.com": {
        "accept_lang": "en-US,en;q=0.9",
        "comment": "Comment", "shared": "Shared by"
    }
}
DEFAULT_LOCALE = {
    "accept_lang": "en-US,en;q=0.9",
    "comment": "Comment", "shared": "Shared by"
}

# --- 3. View クラス (ボタン管理) ---

class CancelView(View):
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.is_cancelled = False

    @discord.ui.button(label="キャンセル (Cancel)", style=discord.ButtonStyle.danger)
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.is_cancelled = True
        self.stop()
        await interaction.response.send_message("❌ 変換をキャンセルしました。", ephemeral=True)
        try: await interaction.message.delete()
        except: pass

class PostProcessView(View):
    def __init__(self, original_content, author_id, timeout=None):
        super().__init__(timeout=timeout)
        self.original_content = original_content
        self.author_id = author_id

    @discord.ui.button(label="🗑️ 削除 (Delete)", style=discord.ButtonStyle.danger)
    async def delete_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("投稿者本人のみ削除可能です。", ephemeral=True)
        await interaction.message.delete()

    @discord.ui.button(label="↩️ 変換を戻す (Undo)", style=discord.ButtonStyle.secondary)
    async def undo_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("投稿者本人のみ操作可能です。", ephemeral=True)
        await interaction.message.delete()
        await interaction.channel.send(self.original_content)

# --- 4. 処理関数 ---

def get_og_data(url):
    try:
        clean_url = url.split('?')[0]
        res = requests.get(clean_url, headers=BASE_HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        title_tag = soup.find('meta', property='og:title') or soup.find('title')
        title = title_tag['content'] if title_tag and title_tag.has_attr('content') else (title_tag.text if title_tag else "Link")
        img_tag = soup.find('meta', property='og:image')
        img_url = img_tag['content'] if img_tag else ""
        return title.strip()[:60], img_url, clean_url
    except:
        return "Link", "", url.split('?')[0]

def scrape_amazon_data(url):
    try:
        domain = next((d for d in LOCALE_SETTINGS if d in url), "amazon.com")
        config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
        headers = BASE_HEADERS.copy()
        headers['Accept-Language'] = config['accept_lang']
        
        session = requests.Session()
        res = session.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title_elem = soup.find(id='productTitle')
        title = title_elem.get_text().strip() if title_elem else "Amazon Product"
        
        img_url = ""
        scripts = soup.find_all('script')
        for s in scripts:
            if 'colorImages' in s.text:
                m = re.search(r'"hiRes":"(https://[^"]+\.jpg)"', s.text)
                if m: img_url = m.group(1); break
        
        if not img_url:
            img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
            img_url = img.get('src') if img and not img.get('content') else (img.get('content') if img else "")

        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
        asin = asin_match.group(1) if asin_match else None
        
        return title[:80], img_url, asin, domain
    except:
        return "Amazon Product", "", None, "amazon.co.jp"

def process_amazon(url, author, user_comment):
    title, img, asin, domain = scrape_amazon_data(url)
    config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
    
    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    if user_comment:
        embed.description = f"**{config['comment']}:**\n{user_comment}"
    
    # 画像を右側に小さく表示 (set_thumbnail)
    if img: embed.set_thumbnail(url=img)
    embed.set_footer(text=f"{config['shared']} {author.display_name} | {domain}")
    return embed

# --- 5. Botメインロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'======================================')
    print(f'✅ {bot.user} Online')
    print(f'🚀 VERSION: 4.4 (Compact Mode)')
    print(f'======================================')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    await asyncio.sleep(0.5)
    target_url = found_urls[0].lower()
    
    # 除外リスト
    exclude_domains = ["youtube.com", "youtu.be", "twitter.com", "x.com", "instagram.com", "tiktok.com", "steampowered.com", "steamcommunity.com"]
    if any(domain in target_url for domain in exclude_domains): return

    view = CancelView()
    status_msg = await message.channel.send(f"⌛ **Analyzing...**", view=view)
    
    clean_comment = message.content
    for u in found_urls:
        clean_comment = clean_comment.replace(u, "")
    clean_comment = clean_comment.strip()

    if "amazon." in target_url or "amzn." in target_url:
        loop = asyncio.get_event_loop()
        embed = await loop.run_in_executor(None, process_amazon, found_urls[0], message.author, clean_comment)
        
        if view.is_cancelled: return

        if embed:
            try:
                post_view = PostProcessView(original_content=message.content, author_id=message.author.id)
                await message.delete()
                await status_msg.edit(content=None, embed=embed, view=post_view)
                return
            except: pass

    elif len(target_url) > 60 or any(d in target_url for d in ["aliexpress", "rakuten", "yahoo"]):
        loop = asyncio.get_event_loop()
        title, img, clean_link = await loop.run_in_executor(None, get_og_data, found_urls[0])
        
        if view.is_cancelled: return

        domain_match = re.search(r'https?://([^/]+)', clean_link)
        domain = domain_match.group(1) if domain_match else "Link"
        desc = f"[{domain}]({clean_link})"
        if clean_comment: desc = f"**Comment:**\n{clean_comment}\n\n" + desc

        short_embed = discord.Embed(title=f"🔗 {title}", description=desc, color=0xcccccc)
        if img: short_embed.set_thumbnail(url=img)
        short_embed.set_footer(text=f"Shared by {message.author.display_name}")

        try:
            post_view = PostProcessView(original_content=message.content, author_id=message.author.id)
            await message.delete()
            await status_msg.edit(content=None, embed=short_embed, view=post_view)
        except: pass
    else:
        if not view.is_cancelled:
            await status_msg.delete()

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)