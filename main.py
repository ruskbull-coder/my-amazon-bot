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

# --- 1. Webサーバー設定 (Keep Alive用) ---
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

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9,ja;q=0.8', 
}

# 多言語・通貨設定の辞書
LOCALE_SETTINGS = {
    "amazon.co.jp": {
        "lang": "ja", "currency": "￥", "price": "価格", 
        "rating": "評価", "reviews": "レビュー数", "comment": "コメント", "shared": "投稿者"
    },
    "amazon.com": {
        "lang": "en", "currency": "$", "price": "Price", 
        "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
    },
    "amazon.co.uk": {
        "lang": "en", "currency": "£", "price": "Price", 
        "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
    }
}
DEFAULT_LOCALE = {
    "lang": "en", "currency": "$", "price": "Price", 
    "rating": "Rating", "reviews": "Reviews", "comment": "Comment", "shared": "Shared by"
}

# --- 3. 処理関数 ---

def get_og_data(url):
    """一般サイトのデータ取得 & パラメータ削除"""
    try:
        clean_url = url.split('?')[0]
        res = requests.get(clean_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title_tag = soup.find('meta', property='og:title') or soup.find('title')
        title = title_tag['content'] if title_tag and title_tag.has_attr('content') else (title_tag.text if title_tag else "Link")
        
        img_tag = soup.find('meta', property='og:image')
        img_url = img_tag['content'] if img_tag else ""
        
        return title.strip()[:60], img_url, clean_url
    except:
        return "Link", "", url.split('?')[0]

def scrape_amazon_data(url):
    """Amazonの商品情報を取得"""
    try:
        time.sleep(1) # 負荷軽減
        res = requests.Session().get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title = soup.find(id='productTitle')
        title = title.get_text().strip() if title else "Amazon Product"
        
        # 価格取得
        price_elem = soup.select_one('.a-price .a-offscreen') or soup.select_one('.a-price-whole')
        price = price_elem.get_text().strip() if price_elem else "N/A"

        # 評価取得
        rating = "N/A"
        r_elem = soup.select_one('span.a-icon-alt')
        if r_elem:
            m = re.search(r'(\d[\.,]\d)', r_elem.get_text())
            if m: rating = f"⭐ {m.group(1)}"

        # レビュー数取得
        reviews = "0"
        rev_elem = soup.find(id='acrCustomerReviewText')
        if rev_elem:
            c = re.sub(r'\D', '', rev_elem.get_text())
            if c: reviews = "{:,}".format(int(c))
        
        # 画像取得
        img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
        img_url = img.get('src') if img and not img.get('content') else (img.get('content') if img else "")
        
        # ASIN抽出（アフィリエイトリンク用）
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
        asin = asin_match.group(1) if asin_match else None
        
        return title[:50], price, rating, reviews, img_url, asin
    except:
        return None

def process_amazon(url, author, user_comment):
    """Amazon用UI (言語・通貨自動切り替え)"""
    data = scrape_amazon_data(url)
    if not data: return None
    
    title, price, rating, reviews, img, asin = data
    
    # URLからドメインを特定
    domain = next((d for d in LOCALE_SETTINGS if d in url), "amazon.com")
    config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
    
    # 価格表示の整形 (数字を抽出して記号と結合)
    clean_price_num = re.sub(r'[^\d\.,]', '', price)
    display_price = f"{config['currency']}{clean_price_num}" if clean_price_num else price

    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    
    if user_comment:
        embed.description = f"**{config['comment']}:**\n{user_comment}"
    
    # 言語設定に基づいたフィールド追加
    embed.add_field(name=config['price'], value=display_price, inline=True)
    embed.add_field(name=config['rating'], value=rating, inline=True)
    embed.add_field(name=config['reviews'], value=reviews, inline=True)
    
    if img: embed.set_thumbnail(url=img)
    
    # フッターの動的変更
    embed.set_footer(text=f"{config['shared']} {author.display_name} | {domain}")
    return embed

# --- 4. Botメインロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'✅ {bot.user} Online (Multilingual Mode)')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    status_msg = await message.channel.send("⌛ **Processing Link...**")
    target_url = found_urls[0]
    
    # コメント抽出
    clean_comment = message.content
    for u in found_urls:
        clean_comment = clean_comment.replace(u, "")
    clean_comment = clean_comment.strip()

    # A. Amazon判定
    if "amazon." in target_url or "amzn." in target_url:
        embed = process_amazon(target_url, message.author, clean_comment)
        if embed:
            try:
                await message.delete()
                await status_msg.edit(content=None, embed=embed)
                return
            except: pass

    # B. Amazon以外
    if len(target_url) > 60 or any(d in target_url for d in ["aliexpress", "rakuten", "yahoo"]):
        title, img, clean_link = get_og_data(target_url)
        domain_match = re.search(r'https?://([^/]+)', clean_link)
        domain = domain_match.group(1) if domain_match else "Link"
        
        desc = f"[{domain}]({clean_link})"
        if clean_comment:
            desc = f"**Comment:**\n{clean_comment}\n\n" + desc

        short_embed = discord.Embed(title=f"🔗 {title}", description=desc, color=0xcccccc)
        if img: short_embed.set_thumbnail(url=img)
        short_embed.set_footer(text=f"Shared by {message.author.display_name}")

        try:
            await message.delete()
            await status_msg.edit(content=None, embed=short_embed)
        except: pass
    else:
        await status_msg.delete()

# --- 5. 実行 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)