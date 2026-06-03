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

# --- 1. Webサーバー設定 (Koyeb Health Check用) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is running! Presence: Online"

def run_web():
    port = int(os.environ.get("PORT", 8000))
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

# --- 3. View クラス ---

async def restore_as_user(channel, user: discord.Member | discord.User, content: str):
    """
    Webhookを使って、指定ユーザーの名前・アイコンでメッセージを再送信する。
    Webhookが作れない場合はボット名義にフォールバックする。
    """
    try:
        # チャンネルのWebhook一覧を取得（なければ新規作成）
        webhooks = await channel.webhooks()
        webhook = discord.utils.get(webhooks, name="RestoreBot")
        if webhook is None:
            webhook = await channel.create_webhook(name="RestoreBot")

        await webhook.send(
            content=content,
            username=user.display_name,
            avatar_url=user.display_avatar.url,
        )
    except discord.Forbidden:
        # Webhook権限がない場合はボット名義で代替
        await channel.send(f"[{user.display_name}の投稿を復元]\n{content}")
    except Exception as e:
        print(f"Webhook Error: {e}")
        await channel.send(content)


class CancelView(View):
    def __init__(self, original_content, author_id, author: discord.Member | discord.User, timeout=30):
        super().__init__(timeout=timeout)
        self.is_cancelled = False
        self.original_content = original_content
        self.author_id = author_id
        self.author = author          # ← Webhookで名前・アイコンに使う
        self.status_msg = None 

    @discord.ui.button(label="キャンセル (Cancel)", style=discord.ButtonStyle.danger)
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 投稿者本人のみキャンセル可能
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("投稿者本人のみキャンセル可能です。", ephemeral=True)

        self.is_cancelled = True
        self.stop()

        # 1. ボットの一時メッセージ（Analyzing...）を削除
        if self.status_msg:
            try:
                await self.status_msg.delete()
            except:
                pass
        else:
            try:
                await interaction.message.delete()
            except:
                pass

        # 2. Webhookで本人名義・アイコンのまま元のメッセージを復元
        await restore_as_user(interaction.channel, self.author, self.original_content)

        # 完了通知（本人にのみ表示）
        await interaction.response.send_message("❌ 変換をキャンセルし、元の状態に戻しました。", ephemeral=True)


class PostProcessView(View):
    def __init__(self, original_content, author_id, author: discord.Member | discord.User, timeout=None):
        super().__init__(timeout=timeout)
        self.original_content = original_content
        self.author_id = author_id
        self.author = author

    @discord.ui.button(label="🗑️ 削除 (Delete)", style=discord.ButtonStyle.danger)
    async def delete_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("投稿者本人のみ削除可能です。", ephemeral=True)
        await interaction.message.delete()
        await interaction.response.send_message("🗑️ 投稿を削除しました。", ephemeral=True)

    @discord.ui.button(label="↩️ 変換を戻す (Undo)", style=discord.ButtonStyle.secondary)
    async def undo_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message("投稿者本人のみ操作可能です。", ephemeral=True)
        await interaction.message.delete()
        await restore_as_user(interaction.channel, self.author, self.original_content)
        await interaction.response.send_message("↩️ 元の状態に戻しました。", ephemeral=True)

# --- 4. 処理関数 ---

def get_og_data(url):
    try:
        res = requests.get(url, headers=BASE_HEADERS, timeout=10, allow_redirects=True)
        clean_url = res.url.split('?')[0]
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
        asin_match = re.search(r'/(?:dp|gp/product|product|ASIN)/([A-Z0-9]{10})', url)
        target_asin = asin_match.group(1) if asin_match else None
        
        domain = next((d for d in LOCALE_SETTINGS if d in url.lower()), "amazon.co.jp")
        
        if target_asin and "amzn." not in url:
            request_url = f"https://{domain}/dp/{target_asin}"
        else:
            request_url = url

        session = requests.Session()
        res = session.get(request_url, headers=BASE_HEADERS, timeout=15, allow_redirects=True)
        final_url = res.url
        
        config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
        headers = BASE_HEADERS.copy()
        headers['Accept-Language'] = config['accept_lang']
        
        res = session.get(final_url, headers=headers, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        title_elem = soup.find(id='productTitle')
        title = title_elem.get_text().strip() if title_elem else "Amazon Product"
        
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

        asin_match_final = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', final_url)
        asin = asin_match_final.group(1) if asin_match_final else target_asin
        
        return title[:80], img_url, asin, domain
    except Exception as e:
        print(f"Scrape Error: {e}")
        return "Amazon Product", "", None, "amazon.co.jp"

def process_amazon(url, author, user_comment):
    title, img, asin, domain = scrape_amazon_data(url)
    config = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
    
    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged_url = f"{clean_url}?tag={AMAZON_TAG}"
    
    embed = discord.Embed(title=title, url=tagged_url, color=0xff9900)
    if user_comment:
        embed.description = f"**{config['comment']}:**\n{user_comment}"
    
    if img:
        embed.set_thumbnail(url=img)
    embed.set_footer(text=f"{config['shared']} {author.display_name} | {domain}")
    return embed

# --- 5. Botロジック ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'======================================')
    print(f'✅ {bot.user} Online')
    print(f'🚀 VERSION: 4.9 (Webhook Cancel Restore)')
    print(f'======================================')

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls: return

    await asyncio.sleep(0.5)
    target_url = found_urls[0].lower()
    
    exclude_domains = ["youtube.com", "youtu.be", "twitter.com", "x.com", "instagram.com", "tiktok.com", "steampowered.com", "steamcommunity.com"]
    if any(domain in target_url for domain in exclude_domains): return

    # author を CancelView に渡す（Webhook復元に使用）
    view = CancelView(
        original_content=message.content,
        author_id=message.author.id,
        author=message.author,
    )
    status_msg = await message.channel.send(f"⌛ **Analyzing...**", view=view)
    view.status_msg = status_msg 
    
    clean_comment = message.content
    for u in found_urls:
        clean_comment = clean_comment.replace(u, "")
    clean_comment = clean_comment.strip()

    if any(x in target_url for x in ["amazon.", "amzn."]):
        loop = asyncio.get_event_loop()
        embed = await loop.run_in_executor(None, process_amazon, found_urls[0], message.author, clean_comment)
        
        if view.is_cancelled: return

        if embed:
            try:
                post_view = PostProcessView(original_content=message.content, author_id=message.author.id, author=message.author)
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
            post_view = PostProcessView(original_content=message.content, author_id=message.author.id, author=message.author)
            await message.delete()
            await status_msg.edit(content=None, embed=short_embed, view=post_view)
        except: pass
    else:
        if not view.is_cancelled:
            try: await status_msg.delete()
            except: pass

if __name__ == "__main__":
    keep_alive()
    if TOKEN:
        bot.run(TOKEN)
