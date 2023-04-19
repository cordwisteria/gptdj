import os
import json
import requests
import configparser
import random
import time
import openai
import webbrowser
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from obswebsocket import obsws, requests as obs_requests
from googleapiclient.discovery import build

# YouTube APIの認証を行う関数
def youtube_auth(config_file):
    # 設定ファイルから認証情報を読み込む
    config = configparser.ConfigParser()
    config.read(config_file)
    api_key = config['YouTube']['api_key']

    # YouTube APIクライアントを構築する
    youtube = build('youtube', 'v3', developerKey=api_key)

    return youtube

# YouTube Live Chat IDを取得する関数
def get_live_chat_id(youtube, video_id):
    video_response = youtube.videos().list(
        part="liveStreamingDetails",
        id=video_id,
    ).execute()

    if not video_response.get("items"):
        print("アクティブなライブ配信が見つかりませんでした。")
        return None

    live_chat_id = video_response["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    return live_chat_id

# YouTube Live Chat APIでチャットメッセージを取得する関数
def get_live_chat_messages(youtube, live_chat_id, next_page_token=None):
    # YouTube Live Chat APIからチャットメッセージを取得
    request_params = {
        "liveChatId": live_chat_id,
        "part": "id,snippet,authorDetails",
    }
    if next_page_token:
        request_params["pageToken"] = next_page_token

    response = youtube.liveChatMessages().list(**request_params).execute()

    return response.get("items", []), response.get("nextPageToken")


# チャットメッセージから選曲リクエストを抽出する関数
def filter_requests(chat_messages):
    song_requests = []
    for message in chat_messages:
        text = message['snippet']['displayMessage']
        if text.startswith('/dj'):
            request_text = text[4:].strip()
            song_requests.append(request_text)

    return song_requests

# ChatGPT APIにリクエストを送信する関数
def gpt_request(api_key, prompt):
    openai.api_key = api_key

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": prompt}, 
        ]
    #   response = openai.Completion.create(
    #     engine="davinci",
    #     prompt=prompt,
    #     max_tokens=50,
    #     n=1,
    #     stop=None,
    #     temperature=0.8
    )

    print(f"\nプロンプト↓↓↓----------------------------------- \n{prompt}\n")
    # print(f"レスポンス: {response.choices[0].text.strip()}")
    #print("レスポンス:" + json.dumps(response, ensure_ascii=False) + "\n")
    print(f"GPTの回答: {response.choices[0]['message']['content']}\n")
    completions = response.choices
    return completions[0]['message']['content'].replace('\n', '')
#    return completions[0].text.strip()


# Spotify APIの認証を行う関数
def spotify_auth(config_file):
    config = configparser.ConfigParser()
    config.read(config_file)
    client_id = config['Spotify']['client_id']
    client_secret = config['Spotify']['client_secret']

    auth_url = 'https://accounts.spotify.com/api/token'
    auth_response = requests.post(auth_url, {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    })

    auth_response.raise_for_status()
    access_token = auth_response.json()['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }

    return headers

# Spotify APIで曲を検索し、再生する関数
def search_and_play_song(headers, song_query):
    search_url = 'https://api.spotify.com/v1/search'
    search_params = {
        'q': song_query,
        'type': 'track',
        'limit': 1
    }
    search_response = requests.get(search_url, headers=headers, params=search_params)
    search_response.raise_for_status()
    tracks = search_response.json()['tracks']['items']
    if not tracks:
        print(f"曲が見つかりませんでした: {song_query}")
        return None
    track = tracks[0]
    #print(f"\ntrack: {track}\n")
    track_name = track['name']
    artist_name = track['artists'][0]['name']
    track_url = track['external_urls']['spotify']
    print(f"再生中: {track_name} - {artist_name}")
#    print(f"URL: {track_url}\n------------------------------------------------")
    webbrowser.open(track_url, new=2, autoraise=True)
    return track_url

# OBS Websocketに接続する関数
def obs_connect(config_file):
    config = configparser.ConfigParser()
    config.read(config_file)
    websocket_port = config.getint('OBS', 'websocket_port')
    websocket_password = config['OBS']['websocket_password']

    ws = obsws('localhost', websocket_port, websocket_password)
    ws.connect()

    return ws

# OBSのテキストソースを更新する関数
def update_obs_text(ws, source_name, text):
    request = obs_requests.SetSourceSettings(source=source_name, text=text)
    ws.call(request)


def main():
    # request.txtファイルを空にします
    #with open('requests.txt', 'w') as f:
    #    f.write('')
    # アプリケーションの初期化と設定を行います
    config_file = 'config.ini'
    youtube = youtube_auth(config_file)
    config = configparser.ConfigParser()
    config.read(config_file)
    chatgpt_api_key = config['ChatGPT']['api_key']

    spotify_headers = spotify_auth(config_file)
    #ws = obs_connect(config_file)

    video_id = input("配信IDを入力してください: ") 
    live_chat_id = get_live_chat_id(youtube, video_id)
    if live_chat_id is None:
        exit(1)
    #text_source_name = input("OBSのテキストソース名を入力してください: ")

    next_page_token = None

    # メインのループ処理を実装します
    while True:
        # YouTube Live Chat APIと連携し、チャットメッセージを取得します
        chat_messages, next_page_token = get_live_chat_messages(youtube, live_chat_id, next_page_token)

        # 選曲リクエストを抽出し、テキストファイルに保存します
        song_requests = filter_requests(chat_messages)
        # requests.txtから既存のリクエストと再生済みのリクエストを読み込みます
        with open('requests.txt', 'r') as f:
            existing_requests = [line.strip() for line in f.readlines()]

        played_requests = [re.sub(r'\*$', '', line.strip()) for line in existing_requests if line.strip().endswith('*')]

        # 既存のリストにない選曲リクエストで、再生済みのリクエストでもないもののみ追加します
        for request in song_requests:
            if request not in existing_requests and request not in played_requests:
                with open('requests.txt', 'a') as f:
                    f.write(request + '\n')

        # 保存されたリクエストを読み込みます
        with open('requests.txt', 'r') as f:
            lines = f.readlines()

        # 再生済みマーク(*)がついていないリクエストのみを抽出します
        unplayed_requests = [line.strip() for line in lines if not line.strip().endswith('*')]

        # ランダムにリクエストを選びます
        if not unplayed_requests:
            time.sleep(10)
            continue

        request_text = random.choice(unplayed_requests)


        # ChatGPT APIにリクエストを送信し、レスポンスを解析します
        prompt = f'依頼：リクエスト「{request_text}」に答えて1曲だけ選曲してください\n出力形式：曲名 アーティスト名\n※出力形式以外のテキストを回答に含めないでください。'
        song_query = gpt_request(chatgpt_api_key, prompt)

        # Spotify APIで曲を検索し、再生
        track_url = search_and_play_song(spotify_headers, song_query)

        # OBS Websocketでテキストソースを更新します
        # update_obs_text(ws, text_source_name, f"Playing: {song_query}")

        # 1分間の再生が終わった時点で溜まったリクエストからランダムに1つをピックアップ
        time.sleep(10)

        # 再生済みリクエストにマークをつけます
        with open('requests.txt', 'w') as f:
            for line in lines:
                if line.strip() == request_text:
                    f.write(line.strip() + "*\n")
                else:
                    f.write(line)
        # request.txtの内容を全て出力します
        with open('requests.txt', 'r') as f:
            content = f.read()
        print("\n↓↓↓リクエストリスト↓↓↓")
        print(f"{content}\n")

if __name__ == "__main__":
    main()
