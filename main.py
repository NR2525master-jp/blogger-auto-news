import feedparser
import json
import urllib.request
import urllib.parse
import os
import time
import sys

# ----------------------------------------------------
# 1. GitHub Secrets からの環境変数読み込み
# ----------------------------------------------------
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
BLOG_ID = os.environ.get('BLOG_ID')
CLIENT_ID = os.environ.get('CLIENT_ID')
CLIENT_SECRET = os.environ.get('CLIENT_SECRET')
REFRESH_TOKEN = os.environ.get('REFRESH_TOKEN')

RSS_URL = "https://www.marktechpost.com/feed/"
HISTORY_FILE = "posted_links.txt"
MAX_POSTS = 5

# 必須の環境変数が揃っているかチェック
if not all([GEMINI_API_KEY, BLOG_ID, CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
    print("❌ エラー: GitHub Secretsの設定が不足しています。")
    sys.exit(1)

# ----------------------------------------------------
# 2. 履歴ファイルの読み込み
# ----------------------------------------------------
posted_links = set()
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        posted_links = set(line.strip() for line in f if line.strip())
print(f"📖 履歴確認: 過去に {len(posted_links)} 件の記事を処理済みです。")

# ----------------------------------------------------
# 3. RSSから未投稿の記事だけを抽出 (最大5件)
# ----------------------------------------------------
print("📡 海外のRSSフィードを取得中...")
feed = feedparser.parse(RSS_URL)

target_entries = []
for entry in feed.entries[::-1]:  # 古い順に処理
    if entry.link not in posted_links:
        target_entries.append(entry)
        if len(target_entries) >= MAX_POSTS:
            break

if not target_entries:
    print("✨ 新着記事はありませんでした。すべて処理済みです。")
    sys.exit(0)

print(f"🔥 新着記事を {len(target_entries)} 件検出しました。処理を開始します。")

# ----------------------------------------------------
# 4. リフレッシュトークンを使ってアクセストークンを自動自動更新
# ----------------------------------------------------
print("🔑 アクセストークンを自動更新中...")
try:
    token_url = "https://oauth2.googleapis.com/token"
    token_data = urllib.parse.urlencode({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token'
    }).encode('utf-8')
    
    token_req = urllib.request.Request(token_url, data=token_data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(token_req) as token_res:
        tokens = json.loads(token_res.read().decode('utf-8'))
        access_token = tokens.get('access_token')
except Exception as e:
    print(f"❌ 認証トークンの更新に失敗しました: {e}")
    sys.exit(1)

# ----------------------------------------------------
# 5. メインループ（自動リトライ付きGemini要約 ➔ Blogger下書き）
# ----------------------------------------------------
success_links = []

for i, entry in enumerate(target_entries, 1):
    title_en = entry.title
    summary_en = entry.summary
    link = entry.link
    
    print(f"\n--- 🔄 [{i}/{len(target_entries)}] 記事処理中 ---")
    print(f"元タイトル: {title_en}")
    
    # 💡 Gemini API 呼び出し（503エラー対策のリトライロジック）
    ai_response = None
    prompt = f"以下の海外の最新AIニュース（英語）を読み、日本の開発者向けに分かりやすく日本語で要約してください。Bloggerにそのまま投稿するため、出力は指定された【HTML形式】の本文のみにしてください。\n\n【出力HTML構成案】\n<h2>タイトル（魅力的な日本語翻訳）</h2>\n<p>ニュースの簡単な概要文（1〜2文）</p>\n<h3>主要な注目ポイント</h3>\n<ul>\n  <li>要点1</li>\n  <li>要点2</li>\n  <li>要点3</li>\n</ul>\n<h3>エンジニア目線での考察</h3>\n<p>今後の影響（1〜2文）</p>\n\n【英語記事】\nタイトル: {title_en}\n内容: {summary_en}"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    req = urllib.request.Request(url, data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8"), headers={"Content-Type": "application/json"})
    
    for attempt in range(1, 4):  # 最大3回リトライ
        try:
            print(f"🤖 Gemini APIで翻訳・要約中... (試行 {attempt}/3)")
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                ai_response = result['candidates'][0]['content']['parts'][0]['text']
                break  # 成功したらリトライループを抜ける
        except urllib.error.HTTPError as e:
            if e.code == 503 and attempt < 3:
                print("⏳ Googleサーバー混雑(503)を検知。5秒後に再試行します...")
                time.sleep(5)
            else:
                print(f"❌ Gemini APIエラー (ステータスコード: {e.code})")
                break
        except Exception as e:
            print(f"❌ 予期せぬエラー: {e}")
            break
            
    if not ai_response:
        print("⏭️ エラーが解消されなかったため、この記事はスキップします。")
        continue
        
    # Bloggerへの投稿用データ整形
    blog_title = f"【最新AI速報】{title_en}"
    blog_content = ai_response + f'<br><p>元記事（英語）：<a href="{link}" target="_blank">{title_en}</a></p>'
    
    # Bloggerへ下書き送信
    try:
        print("📝 Bloggerへ下書きを送信中...")
        post_url = f"https://www.googleapis.com/blogger/v3/blogs/{BLOG_ID}/posts/?isDraft=true"
        post_data = json.dumps({
            'kind': 'blogger#post',
            'title': blog_title,
            'content': blog_content,
            'labels': ['AIニュース', '海外テック']
        }).encode('utf-8')
        
        post_req = urllib.request.Request(post_url, data=post_data, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        })
        
        with urllib.request.urlopen(post_req) as post_res:
            print(f"✨ 投稿成功: {blog_title}")
            success_links.append(link)
            
        time.sleep(2)  # 連続投稿の負荷軽減
    except Exception as e:
        print(f"❌ Bloggerへの送信に失敗しました: {e}")

# ----------------------------------------------------
# 6. 履歴ファイルの更新（成功したURLを追記）
# ----------------------------------------------------
if success_links:
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        for link in success_links:
            f.write(link + "\n")
    print(f"\n💾 履歴ファイルを更新しました。新たに {len(success_links)} 件のURLを記録しました。")

print("\n=== 🎉 すべての処理が完了しました！ ===")
