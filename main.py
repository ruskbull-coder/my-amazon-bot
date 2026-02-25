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

# --- 1. Render用Webサーバー設定 (スリープ & タイムアウト防止) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and healthy!"

def run_web():
    # Renderは環境変数PORTを介してポートを指定します
    port = int(os.environ.get("PORT", 8080))
    # host='0.0.0.0'はRenderの外部スキャンに応答するために必須です
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 2. 設定読み込み ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

# Amazonのブロックを回避するための詳細なヘッダー
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0',
}

CURRENCY_MAP = {
    'co.jp': '￥', 'com': '$', 'co.uk': '£', 'de': '€', 'fr': '€', 'it': '€', 'es': '€', 'ca': 'CA$',
}

def truncate_text(text, length=60):
    if not text: return "Amazon Product"
    return (text[:length] + '...') if len(text) > length else text

# --- 3. スクレイピング関数 ---
def scrape_amazon_localized(url):
    session = requests.Session()
    domain_match = re.search(r'amazon\.([a-z\.]+)', url)
    domain_suffix = domain_match.group(1) if domain_match else 'co.jp'
    
    cookies = {'i18n-prefs': 'JPY', 'lc-main': 'ja_JP'} if domain_suffix == 'co.jp' else {}

    try:
        # 人間味を出すための微小な待機
        time.sleep(1)
        response = session.get(url, headers=HEADERS, cookies=cookies, timeout=15)
        
        if response.status_code != 200:
            print(f"⚠️ Amazon Access Denied: {response.status_code}")
            return "Amazon Product", "N/A", "N/A", "0", "", None, f"amazon.{domain_suffix}"

        soup = BeautifulSoup(response.text, 'html.parser')
        final_url = response.url
        
        # タイトル
        title_elem = soup.find(id='productTitle') or soup.find('meta', property='og:title')
        title_text = "Amazon Product"
        if title_elem:
            title_text = title_elem.get_text().strip() if not title_elem.get('content') else title_elem.get('content')
            title_text = re.sub(r'Amazon\.(co\.jp|com):?\s?', '', title_text)

        # 価格
        price_elem = soup.select_one('.a-price .a-offscreen') or soup.select_one('.a-price-whole')
        price_raw = price_elem.get_text().strip() if price_elem else "N/A"
        
        symbol = CURRENCY_MAP.get(domain_suffix, '￥')
        if price_raw != "N/A" and not any(s in price_raw for s in ['￥', '$', '€']):
            price_raw = f"{symbol}{price_raw}"

        # 評価
        rating_val = "N/A"
        rating_elem = soup.select_one('span.a-icon-alt')
        if rating_elem:
            match = re.search(r'(\d[\.,]\d)', rating_elem.get_text())
            if match: rating_val = f"⭐ {match.group(1)}"

        # レビュー数
        review_elem = soup.find(id='acrCustomerReviewText')
        reviews = "0"
        if review_elem:
            count = re.sub(r'\D', '', review_elem.get_text())
            if count: reviews = "{:,}".format(int(count))

        # 画像
        img_elem = soup.find(id='landingImage') or soup.find('meta', property='og:image')
        image = img_elem.get('src') if img_elem and not img_elem.get('content') else (img_elem.get('content') if img_elem else "")

        # ASIN
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', final_url)
        asin = asin_match.group(1) if asin_match else None

        return truncate_text(title_text), price_raw, rating_val, reviews, image, asin, f"amazon.{domain_suffix}"
    except Exception as e:
        print(f"Scraping Error: {e}")
        return "Amazon Product", "N/A", "N/A", "0", "", None, "amazon.co.jp"

# --- 4. メインロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'✅ Bot is online as {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    urls = re.findall(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', message.content)
    if urls and ("amazon" in urls[0] or "amzn" in urls[0]):
        print(f"🔎 Processing: {urls[0]}")
        title, price, rating, reviews, image, asin, domain = scrape_amazon_localized(urls[0])
        
        # リンク生成
        clean_url = f"https://{domain}/dp/{asin}" if asin else urls[0].split('?')[0]
        tagged_url = f"{clean_url}?tag={AMAZON_TAG}" if "co.jp" in domain else clean_url
        
        embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
        embed.add_field(name="価格", value=price, inline=True)
        embed.add_field(name="評価", value=rating, inline=True)
        embed.add_field(name="レビュー", value=reviews, inline=True)
        if image: embed.set_thumbnail(url=image)
        embed.set_footer(text=f"Shared by {message.author.display_name} | {domain}")
        
        try:
            await message.delete()
            await message.channel.send(embed=embed)
        except:
            await message.channel.send(embed=embed)

# --- 5. 起動実行 ---
if __name__ == "__main__":
    print("🚀 Starting Web Server...")
    keep_alive()  # Webサーバーをバックグラウンドで起動
    
    print("🤖 Starting Discord Bot...")
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ Error: DISCORD_TOKEN not found.")