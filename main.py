# =============================================================================
# Amazon Affiliate & URL Shortener Bot
# VERSION: 5.0
# =============================================================================

import os
import re
import logging
import requests
import discord
from discord.ext import commands
from discord.ui import View
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from threading import Thread
from flask import Flask
import asyncio
from urllib.parse import quote

# =============================================================================
# 定数
# =============================================================================
VERSION = "5.0"
ANALYZING_TIMEOUT = 30       # キャンセルボタンの表示秒数
SCRAPE_TIMEOUT    = 15       # スクレイピングのタイムアウト秒数
OG_TIMEOUT        = 10       # OGP取得のタイムアウト秒数
URL_LONG_THRESHOLD = 100      # この文字数以上のURLをEmbedに変換する

EXCLUDE_DOMAINS = [
    "youtube.com", "youtu.be", "twitter.com", "x.com",
    "instagram.com", "tiktok.com", "steampowered.com", "steamcommunity.com",
    "tenor.com", "giphy.com", "imgur.com",  # GIF・画像サービス
    "discordapp.com", "discordapp.net",    # Discordメディアサーバーを追加
]

# スキップ対象とする拡張子
IGNORE_EXTENSIONS = ('.gif', '.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm')

# ドメインごとのEmbed色
DOMAIN_COLORS = {
    "amazon.co.jp": 0xff9900,
    "amazon.com":   0xff9900,
    "rakuten.co.jp":0xbf0000,
    "yahoo.co.jp":  0xff0033,
    "aliexpress":   0xe62e04,
}
DEFAULT_COLOR = 0xcccccc

LOCALE_SETTINGS = {
    "amazon.co.jp": {"accept_lang": "ja-JP,ja;q=0.9", "comment": "コメント", "shared": "投稿者"},
    "amazon.com":   {"accept_lang": "en-US,en;q=0.9", "comment": "Comment",  "shared": "Shared by"},
}
DEFAULT_LOCALE = {"accept_lang": "en-US,en;q=0.9", "comment": "Comment", "shared": "Shared by"}

RAKUTEN_TAG  = os.getenv('RAKUTEN_TAG',  '')
YAHOO_TAG    = os.getenv('YAHOO_TAG',    '')

BASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
}

# =============================================================================
# ロギング設定
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# =============================================================================
# 環境変数
# =============================================================================
load_dotenv()
TOKEN      = os.getenv('DISCORD_TOKEN') or os.getenv('DISCORD_BOT_TOKEN')
AMAZON_TAG = os.getenv('AMAZON_TAG', 'default-tag-22')

# ボットが動作するチャンネルIDを環境変数で絞り込む（カンマ区切り、空なら全チャンネル）
_raw_ch = os.getenv('ACTIVE_CHANNEL_IDS', '')
ACTIVE_CHANNEL_IDS: set[int] = {
    int(x.strip()) for x in _raw_ch.split(',') if x.strip().isdigit()
}

# =============================================================================
# Webサーバー (Koyeb Health Check用)
# =============================================================================
app = Flask('')

@app.route('/')
def home():
    return f"Bot v{VERSION} is running!"

def keep_alive():
    def run():
        port = int(os.environ.get("PORT", 8000))
        app.run(host='0.0.0.0', port=port)
    t = Thread(target=run, daemon=True)
    t.start()

# =============================================================================
# 変換カウンター（メモリ内ログ）
# =============================================================================
stats = {"amazon": 0, "url": 0, "cancel": 0, "undo": 0}

# =============================================================================
# Webhookキャッシュ
# =============================================================================
_webhook_cache: dict[int, discord.Webhook] = {}

async def get_or_create_webhook(channel: discord.TextChannel) -> discord.Webhook | None:
    """チャンネルごとにWebhookをキャッシュして返す。権限がなければNoneを返す。"""
    if channel.id in _webhook_cache:
        return _webhook_cache[channel.id]
    try:
        webhooks = await channel.webhooks()
        wh = discord.utils.get(webhooks, name="RestoreBot")
        if wh is None:
            wh = await channel.create_webhook(name="RestoreBot")
        _webhook_cache[channel.id] = wh
        return wh
    except discord.Forbidden:
        return None
    except Exception as e:
        log.error(f"Webhook取得エラー: {e}")
        return None

async def restore_as_user(
    channel: discord.TextChannel,
    user: discord.Member | discord.User,
    content: str,
) -> None:
    """投稿者の名前・アイコンでメッセージを復元する。URLが含まれていれば OGP Embed も自動生成。"""
    embeds: list[discord.Embed] = []
    
    # URLを抽出してOGP Embedを生成
    found_urls = re.findall(r'https?://[^\s]+', content)
    if found_urls:
        loop = asyncio.get_running_loop()
        try:
            title, img, clean_url = await loop.run_in_executor(
                None, get_og_data, found_urls[0]
            )
            domain_match = re.search(r'https?://([^/]+)', clean_url)
            domain = domain_match.group(1) if domain_match else "Link"
            color = next(
                (v for k, v in DOMAIN_COLORS.items() if k in domain),
                DEFAULT_COLOR,
            )
            embed = discord.Embed(title=f"🔗 {title}", url=clean_url, color=color)
            if img:
                embed.set_thumbnail(url=img)
            embed.set_footer(text=f"Shared by {user.display_name}")
            embeds.append(embed)
        except Exception as e:
            log.warning(f"OGP取得失敗（復元時）: {e}")
    
    wh = await get_or_create_webhook(channel)
    if wh:
        try:
            await wh.send(
                content=content,
                username=user.display_name,
                avatar_url=user.display_avatar.url,
                embeds=embeds,
            )
            return
        except Exception as e:
            log.error(f"Webhook送信エラー: {e}")
    # フォールバック
    if embeds:
        await channel.send(f"[{user.display_name} の投稿を復元]\n{content}", embeds=embeds)
    else:
        await channel.send(f"[{user.display_name} の投稿を復元]\n{content}")

# =============================================================================
# View クラス
# =============================================================================

class CancelView(View):
    def __init__(
        self,
        original_content: str,
        author_id: int,
        author: discord.Member | discord.User,
    ):
        super().__init__(timeout=ANALYZING_TIMEOUT)
        self.is_cancelled   = False
        self.original_content = original_content
        self.author_id      = author_id
        self.author         = author
        self.status_msg: discord.Message | None = None

    @discord.ui.button(label="キャンセル (Cancel)", style=discord.ButtonStyle.danger)
    async def cancel_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "投稿者本人のみキャンセル可能です。", ephemeral=True
            )

        self.is_cancelled = True
        self.stop()
        stats["cancel"] += 1

        # まず interaction に応答
        await interaction.response.send_message(
            "❌ キャンセルしました。元のメッセージを保持します。", ephemeral=True
        )

        # status_msg（Analyzing...）だけ削除、元のメッセージは保持
        if self.status_msg:
            try:
                await self.status_msg.delete()
            except Exception as e:
                log.warning(f"status_msg削除失敗: {e}")


class PostProcessView(View):
    def __init__(
        self,
        original_content: str,
        author_id: int,
        author: discord.Member | discord.User,
    ):
        super().__init__(timeout=None)
        self.original_content = original_content
        self.author_id        = author_id
        self.author           = author

    @discord.ui.button(label="🗑️ 削除 (Delete)", style=discord.ButtonStyle.danger)
    async def delete_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "投稿者本人のみ削除可能です。", ephemeral=True
            )
        await interaction.response.send_message("🗑️ 投稿を削除しました。", ephemeral=True)
        await interaction.message.delete()

    @discord.ui.button(label="↩️ 変換を戻す (Undo)", style=discord.ButtonStyle.secondary)
    async def undo_callback(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "投稿者本人のみ操作可能です。", ephemeral=True
            )
        stats["undo"] += 1
        await interaction.response.send_message("↩️ このメッセージを削除します。元のメッセージを参照してください。", ephemeral=True)
        # ボットが送信したEmbed付きメッセージだけ削除、元のメッセージは保持
        await interaction.message.delete()

# =============================================================================
# スクレイピング / OGP取得
# =============================================================================

def get_og_data(url: str) -> tuple[str, str, str]:
    """OGPからタイトル・画像・クリーンURLを返す。"""
    try:
        res = requests.get(url, headers=BASE_HEADERS, timeout=OG_TIMEOUT, allow_redirects=True)
        clean_url = res.url.split('?')[0]
        soup = BeautifulSoup(res.text, 'html.parser')
        title_tag = soup.find('meta', property='og:title') or soup.find('title')
        title = (
            title_tag.get('content') or title_tag.get_text()
            if title_tag else "Link"
        )
        img_tag = soup.find('meta', property='og:image')
        img_url = img_tag.get('content', '') if img_tag else ''
        return title.strip()[:60], img_url, clean_url
    except Exception as e:
        log.warning(f"OGP取得失敗 ({url}): {e}")
        return "Link", "", url.split('?')[0]


def scrape_amazon(url: str) -> tuple[str, str, str, str, str]:
    """Amazonページから タイトル・画像・価格・ASIN・ドメイン を返す。"""
    try:
        asin_match = re.search(r'/(?:dp|gp/product|product|ASIN)/([A-Z0-9]{10})', url)
        target_asin = asin_match.group(1) if asin_match else None

        domain = next((d for d in LOCALE_SETTINGS if d in url.lower()), "amazon.co.jp")
        request_url = (
            f"https://{domain}/dp/{target_asin}"
            if target_asin and "amzn." not in url
            else url
        )

        session = requests.Session()
        config  = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
        headers = {**BASE_HEADERS, 'Accept-Language': config['accept_lang']}

        res       = session.get(request_url, headers=BASE_HEADERS, timeout=SCRAPE_TIMEOUT, allow_redirects=True)
        final_url = res.url
        res       = session.get(final_url, headers=headers, timeout=SCRAPE_TIMEOUT)
        soup      = BeautifulSoup(res.text, 'html.parser')

        # タイトル
        title_elem = soup.find(id='productTitle')
        title = title_elem.get_text().strip() if title_elem else "Amazon Product"

        # 画像
        img_url = ''
        for s in soup.find_all('script'):
            if 'colorImages' in s.text:
                m = re.search(r'"hiRes":"(https://[^"]+\.jpg)"', s.text)
                if m:
                    img_url = m.group(1)
                    break
        if not img_url:
            img = soup.find(id='landingImage') or soup.find('meta', property='og:image')
            if img:
                img_url = img.get('src') or img.get('content') or ''

        # 価格
        price = ''
        price_elem = (
            soup.find('span', {'class': 'a-price-whole'})
            or soup.find('span', id='priceblock_ourprice')
            or soup.find('span', id='priceblock_dealprice')
        )
        if price_elem:
            raw = price_elem.get_text().strip().replace('\n', '').replace('\xa0', '')
            price = raw[:20]

        # ASIN
        asin_final = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', final_url)
        asin = asin_final.group(1) if asin_final else target_asin

        return title[:80], img_url, price, asin, domain

    except Exception as e:
        log.error(f"Amazonスクレイピングエラー: {e}")
        return "Amazon Product", "", "", None, "amazon.co.jp"


def build_amazon_embed(
    url: str,
    author: discord.Member | discord.User,
    user_comment: str,
) -> discord.Embed:
    title, img, price, asin, domain = scrape_amazon(url)
    config    = LOCALE_SETTINGS.get(domain, DEFAULT_LOCALE)
    clean_url = f"https://{domain}/dp/{asin}" if asin else url.split('?')[0]
    tagged    = f"{clean_url}?tag={AMAZON_TAG}"
    color     = DOMAIN_COLORS.get(domain, DEFAULT_COLOR)

    embed = discord.Embed(title=title, url=tagged, color=color)

    desc_parts = []
    if user_comment:
        desc_parts.append(f"**{config['comment']}:**\n{user_comment}")
    if desc_parts:
        embed.description = "\n\n".join(desc_parts)

    if img:
        embed.set_thumbnail(url=img)
    embed.set_footer(text=f"{config['shared']} {author.display_name} | {domain}")
    stats["amazon"] += 1
    return embed


def build_url_embed(
    url: str,
    author: discord.Member | discord.User,
    user_comment: str,
) -> discord.Embed:
    title, img, clean_link = get_og_data(url)
    domain_match = re.search(r'https?://([^/]+)', clean_link)
    domain = domain_match.group(1) if domain_match else "Link"

    # もしもアフィリエイト経由のリンクに変換
    # 形式: https://af.moshimo.com/af/c/click?a_id=XXXX&p_id=YYY&url=元URL(URLエンコード)
    final_url = clean_link
    if RAKUTEN_TAG and "rakuten.co.jp" in domain:
        encoded = quote(clean_link, safe='')
        final_url = f"https://af.moshimo.com/af/c/click?a_id={RAKUTEN_TAG}&p_id=54&pc_id=54&pl_id=616&url={encoded}"
    elif YAHOO_TAG and "yahoo.co.jp" in domain:
        encoded = quote(clean_link, safe='')
        final_url = f"https://af.moshimo.com/af/c/click?a_id={YAHOO_TAG}&p_id=1&pc_id=1&pl_id=1&url={encoded}"

    color = next(
        (v for k, v in DOMAIN_COLORS.items() if k in domain),
        DEFAULT_COLOR,
    )

    # Amazonと同じ形式：タイトルはクリック可能なURLに
    embed = discord.Embed(title=title, url=final_url, color=color)
    
    if user_comment:
        embed.description = f"**Comment:**\n{user_comment}"
    
    if img:
        embed.set_thumbnail(url=img)
    embed.set_footer(text=f"Shared by {author.display_name} | {domain}")
    stats["url"] += 1
    return embed

# =============================================================================
# Bot
# =============================================================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    log.info("=" * 42)
    log.info(f"✅  {bot.user}  Online  |  v{VERSION}")
    if ACTIVE_CHANNEL_IDS:
        log.info(f"📌  動作チャンネル: {ACTIVE_CHANNEL_IDS}")
    else:
        log.info("📌  動作チャンネル: 全チャンネル")
    log.info("=" * 42)


@bot.command(name="stats")
@commands.has_permissions(manage_messages=True)
async def cmd_stats(ctx):
    """管理者向け: 変換統計を表示する。"""
    embed = discord.Embed(title="📊 変換統計", color=0x5865f2)
    embed.add_field(name="Amazon変換",   value=str(stats["amazon"]), inline=True)
    embed.add_field(name="URL短縮",      value=str(stats["url"]),    inline=True)
    embed.add_field(name="キャンセル",   value=str(stats["cancel"]), inline=True)
    embed.add_field(name="Undo",         value=str(stats["undo"]),   inline=True)
    await ctx.send(embed=embed)


@bot.event
async def on_message(message: discord.Message):
    await bot.process_commands(message)

    if message.author.bot:
        return

    # チャンネル制限
    if ACTIVE_CHANNEL_IDS and message.channel.id not in ACTIVE_CHANNEL_IDS:
        return

    found_urls = re.findall(r'https?://[^\s]+', message.content)
    if not found_urls:
        return

    await asyncio.sleep(0.5)
    target_url = found_urls[0].lower()

    if any(d in target_url for d in EXCLUDE_DOMAINS):
        return

# パラメータ(?...)を除去して拡張子チェック
    clean_path = target_url.split('?')[0]
    if clean_path.endswith(IGNORE_EXTENSIONS):
        return

    # コメント抽出（URLを除いたテキスト）
    clean_comment = message.content
    for u in found_urls:
        clean_comment = clean_comment.replace(u, "")
    clean_comment = clean_comment.strip()

    view = CancelView(
        original_content=message.content,
        author_id=message.author.id,
        author=message.author,
    )
    status_msg = await message.channel.send("⌛ **Analyzing...**", view=view)
    view.status_msg = status_msg

    loop = asyncio.get_running_loop()

    # --- Amazon ---
    if any(x in target_url for x in ["amazon.", "amzn."]):
        try:
            embed = await loop.run_in_executor(
                None, build_amazon_embed, found_urls[0], message.author, clean_comment
            )
        except Exception as e:
            log.error(f"Amazon embed生成失敗: {e}")
            await status_msg.delete()
            return

        if view.is_cancelled:
            return

        post_view = PostProcessView(
            original_content=message.content,
            author_id=message.author.id,
            author=message.author,
        )
        try:
            await message.delete()
            await status_msg.delete()
            wh = await get_or_create_webhook(message.channel)
            if wh:
                await wh.send(
                    embed=embed,
                    view=post_view,
                    username=message.author.display_name,
                    avatar_url=message.author.display_avatar.url,
                )
            else:
                await message.channel.send(embed=embed, view=post_view)
        except Exception as e:
            log.error(f"メッセージ差替え失敗: {e}")

    # --- その他URL（楽天・Yahoo・AliExpress・長いURL）---
    elif len(target_url) > URL_LONG_THRESHOLD or any(
        d in target_url for d in ["aliexpress", "rakuten", "yahoo"]
    ):
        try:
            embed = await loop.run_in_executor(
                None, build_url_embed, found_urls[0], message.author, clean_comment
            )
        except Exception as e:
            log.error(f"URL embed生成失敗: {e}")
            await status_msg.delete()
            return

        if view.is_cancelled:
            return

        post_view = PostProcessView(
            original_content=message.content,
            author_id=message.author.id,
            author=message.author,
        )
        try:
            await message.delete()
            await status_msg.delete()
            wh = await get_or_create_webhook(message.channel)
            if wh:
                await wh.send(
                    embed=embed,
                    view=post_view,
                    username=message.author.display_name,
                    avatar_url=message.author.display_avatar.url,
                )
            else:
                await message.channel.send(embed=embed, view=post_view)
        except Exception as e:
            log.error(f"メッセージ差替え失敗: {e}")

    # --- 対象外URL: Analyzing...を消すだけ ---
    else:
        if not view.is_cancelled:
            try:
                await status_msg.delete()
            except Exception as e:
                log.warning(f"status_msg削除失敗: {e}")


# =============================================================================
if __name__ == "__main__":
    keep_alive()
    if TOKEN:
        bot.run(TOKEN)
    else:
        log.critical("DISCORD_TOKEN が設定されていません。")