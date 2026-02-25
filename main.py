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

# --- 1. Render用Webサーバー ---
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
    'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
}

# --- 3. スクレイピング & 処理関数 ---

def get_og_data(url):
    """一般サイトのOGP（画像・タイトル）を取得"""
    try:
        # 他サイトもスッキリさせるため、?以降をカットしたURLで試行
        clean_url = url.split('?')[0]
        res = requests.get(clean_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title = soup.find('meta', property='og:title')
        title = title['content'] if title else "外部サイト"
        
        img = soup.find('meta', property='og:image')
        img_url = img['content'] if img else ""
        
        return title[:60], img_url, clean_url
    except:
        return "外部リンク", "", url.split('?')[0]

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

        # 評価
        rating = "N/A"
        r_elem = soup.select_one('span.a-icon-alt')
        if r_elem:
            m = re.search(r'(\d[\.,]\d)', r_elem.get_text())
            if m: rating = f"⭐ {m.group(1)}"

        # レビュー数
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
    """Amazon用UIを生成"""
    data = scrape_amazon_data(url)
    if not data: return None
    
    title, price, rating, reviews, img, asin = data
    domain = "amazon.co.jp"
    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    if user_comment:
        embed.description = f"**投稿者のコメント:**\n{user_comment}"
    
    # 送っていただいた画像のUI通り、横並びに配置
    embed.add_field(name="価格", value=price, inline=True)
    embed.add_field(name="評価", value=rating, inline=True)
    embed.add_field(name="レビュー", value=reviews, inline=True)
    
    if img: embed.set_thumbnail(url=img)
    embed.set_footer(text=f"Shared by {author.display_name} | {domain}")
    return embed

# --- 4. Botロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    urls = re.findall(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', message.content)
    if not urls: return

    target_url = urls[0]
    # URL以外の純粋なコメントを抽出
    user_comment = re.sub(r'https?://[\w/:%#\$&\?\(\)~\.=\+\-]+', '', message.content).strip()
    
    status_msg = await message.channel.send("⌛ **リンクを確認中...**")

    # A. Amazonの場合
    if "amazon." in target_url or "amzn." in target_url:
        embed = process_amazon(target_url, message.author, user_comment)
        if embed:
            await message.delete()
            await status_msg.edit(content=None, embed=embed)
            return

    # B. Amazon以外（80文字以上、またはAliExpressなど）
    if len(target_url) > 80 or "aliexpress" in target_url:
        title, img, clean_url = get_og_data(target_url)
        domain = re.search(r'https?://([^/]+)', clean_url).group(1)
        
        desc = f"[{domain} へ移動する]({target_url})"
        if user_comment:
            desc = f"**投稿者のコメント:** {user_comment}\n\n" + desc

        short_embed = discord.Embed(title=f"🔗 {title}", description=desc, color=0xcccccc)
        if img: short_embed.set_thumbnail(url=img)
        short_embed.set_footer(text=f"Shared by {message.author.display_name} | パラメータを削除して短縮しました")

        await message.delete()
        await status_msg.edit(content=None, embed=short_embed)
    else:
        # 短いURLは何もしない
        await status_msg.delete()

# --- 5. 実行 ---
if __name__ == "__main__":
    keep_alive()
    bot.run(TOKEN)