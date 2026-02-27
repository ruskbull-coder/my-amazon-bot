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
import time
import asyncio
import json

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
        "currency": "￥", "price": "価格", 
        "rating": "評価", "reviews": "レビュー数", "comment": "コメント", "shared": "投稿者"
    },
    "amazon.com": {
        "accept_lang": "en-US,en;q=0.9",
        "currency": "$", "price": "Price", 
        "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
    },
    "amazon.co.uk": {
        "accept_lang": "en-GB,en;q=0.9",
        "currency": "£", "price": "Price", 
        "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
    }
}
DEFAULT_LOCALE = {
    "accept_lang": "en-US,en;q=0.9",
    "currency": "$", "price": "Price", 
    "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
}

# --- 3. キャンセルボタン用 View クラス ---
class CancelView(View):
    def __init__(self, timeout=30):
        super().__init__(timeout=timeout)
        self.is_cancelled = False

    @discord.ui.button(label="キャンセル (Cancel)", style=discord.ButtonStyle.danger)
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.is_cancelled = True
        self.stop()
        await interaction.response.send_message("❌ 変換をキャンセルしました。", ephemeral=True)
        try:
            await interaction.message.delete()
        except:
            pass

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
        
        # 高画質画像抽出
        img_url = ""
        scripts = soup.find_all('script')
        for s in scripts:
            if 'colorImages' in s.text:
                m = re.search(r'"hiRes":"(https://[^"]+\.jpg)"', s.text)
                if m:
                    img_url = m.group(1)
                    break
        
        if not img_url:
            img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
            img_url = img.get('src') if img and not img.get('content') else (img.get('content') if img else "")

        # 価格取得
        price = "N/A"
        price_selectors = [
            'span.a-price span.a-offscreen', 
            '#priceblock_ourprice', 
            '#priceblock_dealprice', 
            '.a-color-price',
            '#kindle-price'
        ]
        for selector in price_selectors:
            p_elem = soup.select_one(selector)
            if p_elem and p_elem.get_text().strip():
                price = p_elem.get_text().strip()
                break

        # 評価
        rating = "N/A"
        r_elem = soup.select_one('span.a-icon-alt')
        if r_elem:
            m = re.search(r'(\d[\.,]\d)', r_elem.get_text())
            if m: rating = f"★{m.group(1)}"

        # レビュー数
        reviews = "0"
        rev_elem = soup.find(id='acrCustomerReviewText')
        if rev_elem:
            c = re.sub(r'\D', '', rev_elem.get_text())
            if c: reviews = "{:,}".format(int(c))
        
        # ASIN
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
        asin = asin_match.group(1) if asin_match else None
        
        return title[:80], price, rating, reviews, img_url, asin, domain
    except Exception as e:
        print(f"Scraping Error: {e}")
        return "Amazon Product (Load Error)", "Check Link", "N/A", "0", "", None, "amazon.co.jp"

def process_amazon(url, author, user_comment):
    data = scrape_amazon_data(url)
    if not data: return None
    
    title, price, rating, reviews, img, asin, domain = data
    config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
    
    # 価格の整数化
    if config['currency'] == "￥":
        clean_num = re.sub(r'\D', '', price) 
        display_price = f"{config['currency']}{int(clean_num):,}" if clean_num else price
    else:
        clean_num = re.sub(r'[^\d\.,]', '', price)
        display_price = f"{config['currency']}{clean_num}" if clean_num else price

    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    if user_comment:
        embed.description = f"**{config['comment']}:**\n{user_comment}"
    
    embed.add_field(name=config['price'], value=display_price, inline=True)
    embed.add_field(name=config['rating'], value=rating, inline=True)
    embed.add_field(name=config['reviews'], value=reviews, inline=True)
    
    if img: embed.set_image(url=img) # 画像を大きく表示
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
    print(f'🚀 VERSION: 4.2 (STEAM EXCLUDE ADDED)')
    print(f'======================================')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    # --- 二重投稿・競合対策の待機 ---
    await asyncio.sleep(0.5)

    target_url = found_urls[0].lower()
    
    # 除外リスト (SNS, YouTube, Steam)
    exclude_domains = [
        "youtube.com", "youtu.be", 
        "twitter.com", "x.com", 
        "instagram.com", "tiktok.com",
        "steampowered.com", "steamcommunity.com"
    ]
    if any(domain in target_url for domain in exclude_domains): return

    # キャンセルボタン付きステータスメッセージ
    domain_label = next((d for d in LOCALE_SETTINGS if d in target_url), "Amazon")
    view = CancelView()
    status_msg = await message.channel.send(f"⌛ **Analyzing {domain_label}...**", view=view)
    
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
                await message.delete()
                await status_msg.edit(content=None, embed=embed, view=None)
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
            await message.delete()
            await status_msg.edit(content=None, embed=short_embed, view=None)
        except: pass
    else:
        if not view.is_cancelled:
            await status_msg.delete()

if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)