import os
import re
import requests
import discord
from discord.ext import commands
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Thread
from flask import Flask

# --- 1. Render用Webサーバー設定 ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# --- 2. 設定読み込み ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

# Amazonのブロックを回避するための「人間らしい」ヘッダー
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Referer': 'https://www.google.com/',
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
        response = session.get(url, headers=HEADERS, cookies=cookies, timeout=15)
        # もしAmazonに弾かれたらここで終了
        if response.status_code != 200:
            print(f"⚠️ Amazon Access Denied (Status: {response.status_code})")
            return "Amazon Product", "N/A", "N/A", "0", "", None, f"amazon.{domain_suffix}"

        soup = BeautifulSoup(response.text, 'html.parser')
        final_url = response.url
        
        # 1. 商品名
        title_elem = soup.find(id='productTitle') or soup.find('meta', property='og:title')
        title_text = "Amazon Product"
        if title_elem:
            title_text = title_elem.get_text().strip() if not title_elem.get('content') else title_elem.get('content')
            title_text = title_text.replace('Amazon | ', '').replace('Amazon.com: ', '')

        # 2. 価格
        price_elem = soup.select_one('.a-price .a-offscreen') or soup.select_one('.a-price-whole') or soup.select_one('.a-color-price')
        price_raw = price_elem.get_text().strip() if price_elem else "N/A"
        
        symbol = CURRENCY_MAP.get(domain_suffix, '$')
        if price_raw != "N/A" and not any(s in price_raw for s in ['￥', '$', '£', '€']):
            price_raw = f"{symbol}{price_raw}"

        # 3. 星評価
        rating_val = "N/A"
        selectors = ['span.a-icon-alt', 'span[data-hook="rating-out-of-text"]', '#acrPopover']
        for selector in selectors:
            elem = soup.select_one(selector)
            if elem:
                match = re.search(r'(\d[\.,]\d)', elem.get_text())
                if match:
                    rating_val = f"⭐ {match.group(1)}"
                    break

        # 4. レビュー数
        review_elem = soup.find(id='acrCustomerReviewText')
        reviews = "0"
        if review_elem:
            count = re.sub(r'\D', '', review_elem.get_text())
            if count: reviews = "{:,}".format(int(count))

        # 5. 画像
        img_elem = soup.find(id='landingImage') or soup.find('meta', property='og:image')
        image = img_elem.get('src') if img_elem and not img_elem.get('content') else (img_elem.get('content') if img_elem else "")

        # 6. ASIN抽出
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', final_url)
        asin = asin_match.group(1) if asin_match else None

        return truncate_text(title_text), price_raw, rating_val, reviews, image, asin, f"amazon.{domain_suffix}"
    except Exception as e:
        print(f"Scraping Error: {e}")
        return "Amazon Product", "N/A", "N/A", "0", "", None, "amazon.co.jp"

# --- 4. URL処理 ---
def process_url(url, author):
    if re.search(r'amazon\.|amzn\.', url):
        title, price, rating, reviews, image, asin, domain = scrape_amazon_localized(url)
        clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
        tagged_url = f"{clean_url}?tag={AMAZON_TAG}" if "co.jp" in domain else clean_url
        
        embed = discord.Embed(title=title, url=tagged_url, color=discord.Color.blue())
        embed.add_field(name="Price", value=price, inline=True)
        embed.add_field(name="Rating", value=rating, inline=True)
        embed.add_field(name="Reviews", value=reviews, inline=True)
        
        if image: embed.set_thumbnail(url=image)
        embed.set_footer(text=f"Shared by {author.display_name} | {domain}")
        return embed
    return None

# --- 5. Botイベント ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'✅ ログイン成功: {bot.user}')

@bot.event
async def on_message(message):
    if message.author.bot: return
    urls = re.findall(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', message.content)
    if urls:
        if "amazon" in urls[0] or "amzn" in urls[0]:
            print(f"🔎 検出: {urls[0]}")
            embed = process_url(urls[0], message.author)
            if embed:
                try:
                    await message.delete()
                    await message.channel.send(embed=embed)
                except Exception as e:
                    print(f"❌ エラー: {e}")

# --- 6. 起動実行 ---
if __name__ == "__main__":
    keep_alive()
    if TOKEN:
        print("🚀 Starting Bot...")
        bot.run(TOKEN)
    else:
        print("❌ TOKEN NOT FOUND")