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

# --- 2. 設定 ---
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9', # 英語の結果を取得しやすく設定
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
        time.sleep(1)
        res = requests.Session().get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title = soup.find(id='productTitle')
        title = title.get_text().strip() if title else "Amazon Product"
        
        price = soup.select_one('.a-price .a-offscreen') or soup.select_one('.a-price-whole')
        price = price.get_text().strip() if price else "N/A"

        rating = "N/A"
        r_elem = soup.select_one('span.a-icon-alt')
        if r_elem:
            m = re.search(r'(\d[\.,]\d)', r_elem.get_text())
            if m: rating = f"⭐ {m.group(1)}"

        reviews = "0"
        rev_elem = soup.find(id='acrCustomerReviewText')
        if rev_elem:
            c = re.sub(r'\D', '', rev_elem.get_text())
            if c: reviews = "{:,}".format(int(c))
        
        img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
        img_url = img.get('src') if img and not img.get('content') else (img.get('content') if img else "")
        
        asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
        asin = asin_match.group(1) if asin_match else None
        
        return title[:50], price, rating, reviews, img_url, asin
    except:
        return None

def process_amazon(url, author, user_comment):
    """Amazon用UI (英語表記)"""
    data = scrape_amazon_data(url)
    if not data: return None
    
    title, price, rating, reviews, img, asin = data
    clean_url = f"https://amazon.co.jp/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    if user_comment:
        embed.description = f"**Comment:**\n{user_comment}"
    
    # 英語表記に変更
    embed.add_field(name="Price", value=price, inline=True)
    embed.add_field(name="Rating", value=rating, inline=True)
    embed.add_field(name="Reviews", value=reviews, inline=True)
    
    if img: embed.set_thumbnail(url=img)
    embed.set_footer(text=f"Shared by {author.display_name} | amazon.co.jp")
    return embed

# --- 4. Botメインロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready(): print(f'✅ {bot.user} Online')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    # メッセージ内のURLをすべて検出
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    # ロード中
    status_msg = await message.channel.send("⌛ **Processing Link...**")
    
    target_url = found_urls[0]
    
    # 【重要】URLをメッセージから完全に除去して「純粋なコメント」を抽出
    clean_comment = message.content
    for u in found_urls:
        clean_comment = clean_comment.replace(u, "")
    clean_comment = clean_comment.strip()

    # A. Amazon
    if "amazon." in target_url or "amzn." in target_url:
        embed = process_amazon(target_url, message.author, clean_comment)
        if embed:
            try:
                await message.delete()
                await status_msg.edit(content=None, embed=embed)
                return
            except: pass

    # B. Amazon以外（短縮対象）
    if len(target_url) > 60 or any(d in target_url for d in ["aliexpress", "rakuten", "yahoo"]):
        title, img, clean_link = get_og_data(target_url)
        domain = re.search(r'https?://([^/]+)', clean_link).group(1)
        
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