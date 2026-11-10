# AP Tutor — 応用情報技術者試験 AI個別指導

問題を撮影すると画像をWeb画面へ自動転送し、AIが問題の意味を読み取って対話形式で説明するローカル向けWebアプリです。新しく撮影した画像は永続保存せず、チャットや履歴の表示に必要な間だけサーバーのメモリーに一時保持します。ブラウザーにOpenAI APIキーを渡さず、FastAPIサーバーからOpenAI Responses APIを呼び出します。

## 現在の実装範囲

- 問題画像のアップロードとUbuntuエージェントからの自動受信
- 学習モードON/OFF、画像形式・サイズの検証
- SQLiteへの問題情報と会話の保存、履歴再開・削除
- 画像はRAMに最大12枚または64MBまで一時保持。上限超過・サーバー再起動で画像を破棄
- WebSocketによる新規撮影とAI解析完了の通知
- OpenAIの画像読解とチャット。講師ルールは `tutor-skill/SKILL.md` と参照資料から毎回読み込み
- 科目A/B選択、ヒント段階、明示的な正答開示操作

常駐エージェントはWayland（`wl-paste`）またはX11（`xclip`）のクリップボードを監視し、新しい画像が入るとファイルを作らずWebアプリへ送ります。撮影画像が保存先へ書き出される設定の場合は、従来どおりファイルを検知して送信後に削除します。Fn+F6は「画像をクリップボードへコピー」する撮影操作に設定してください。Ubuntu実機での動作は未確認です。

## 必要な環境

- Python 3.11以上
- Ubuntu Desktop（スクリーンショット自動連携を使う場合）
- クリップボード連携には、Waylandなら `wl-clipboard`、X11なら `xclip`
- OpenAI APIキー（AI解析・対話を使う場合）

## セットアップ

```bash
cd /path/to/ap_study
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp -n .env.sample .env
```

`.env` の `OPENAI_API_KEY` にご自身のAPIキーを設定します。`.env` はGit対象外で、認証情報を含めない `.env.sample` を共有します。モデルは `OPENAI_MODEL` で変更できます。初期値は `gpt-5.4-mini` です。

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

ブラウザーで <http://127.0.0.1:8000> を開きます。問題情報と会話は `data/` のSQLiteデータベースに保存されます。新たに撮影した画像はサーバーのRAMにだけ保存し、ディスクには残しません。

## Ubuntuスクリーンショット連携

### Fn+F6のスクリーンショットを自動転送

クリップボード連携ツールを入れ、プロジェクトのルートでエージェントをログイン時に起動する設定を一度だけ行います。

```bash
sudo apt install wl-clipboard xclip
bash ubuntu_agent/install_service.sh
```

インストールスクリプトは現在のデスクトップセッション変数（WaylandのソケットまたはX11の`DISPLAY`等）をユーザーサービスへ渡してから起動します。コードを更新した場合は、同じスクリプトを再実行してエージェントを再起動してください。

Fn+F6の撮影結果が画像クリップボードに入ると、エージェントが最長約1秒で検知し、画像を一時ファイルにせずWebアプリへ送信します。クリップボードへ画像を入れたままにしても再送しません。撮影画像が保存先へ書き出される設定の場合は、`~/Pictures/Screenshots/`または`~/Pictures/`を監視し、送信後にそのファイルを削除します。保存先が別の場合は`.env`の`AP_TUTOR_SCREENSHOT_DIR`に指定してください。

Fn+F6が保存ダイアログを出す場合やクリップボードに画像を入れない場合は、GNOMEの「設定 → キーボード → キーボードショートカット → カスタムショートカット」でFn+F6へ「画面のスクリーンショットをクリップボードへコピー」相当の撮影操作を割り当ててください。キーがGNOMEに渡らない機種では、Fn+F6が出力するキーを確認し、そのキーを割り当てます。

```bash
bash -lc 'cd /home/yusuke/project/ap_study && .venv/bin/python -m ubuntu_agent.main --capture'
```

この任意の直接撮影経路は一時ファイルをRAM上の`/dev/shm`にだけ作り、送信後に削除します。

エージェントは `POST /api/agent/screenshots` へ送信し、トークンを設定した場合はBearer認証を使います。学習モードがOFFの場合は受付を拒否します。クリップボードの画像はそのまま残り、保存先に作られた撮影ファイルは削除されます。ログ確認は `journalctl --user -u ap-tutor-agent.service -f` を使います。

## API

- `POST /api/screenshots` — `Content-Type: image/png`等の生画像データをブラウザーから受信
- `POST /api/agent/screenshots` — 生画像データをUbuntuエージェントから受信（任意のBearerトークン対応）
- `POST /api/agent/heartbeat` / `GET /api/agent/status` — 撮影エージェントと画像クリップボードの状態
- `GET /api/questions` / `GET /api/questions/{id}` — 履歴と問題
- `GET /api/questions/{id}/messages` — 会話履歴
- `PATCH /api/questions/{id}` — 科目、ヒント段階、正答開示状態
- `POST /api/questions/{id}/chat` — AI講師との会話
- `DELETE /api/questions/{id}` — 問題情報と会話を削除
- `GET /ws` — 新着画像・AI解析のWebSocket通知
- `GET /api/health` — サーバーとAI設定の状態

## マイルストーン

詳細仕様と項目ごとの実装状態は [docs/SPECIFICATION.md](docs/SPECIFICATION.md)、進捗チェックリストは [docs/MILESTONES.md](docs/MILESTONES.md) で管理します。

### Phase 1 — MVP

- [x] FastAPI・ブラウザー画面・SQLiteの基本構成
- [x] 撮影画像の自動送信エージェント（ショートカット撮影後に一時ファイルを削除）
- [x] 画像のWeb画面反映、履歴、会話の再開と削除
- [x] OpenAI画像読解と講師ルール適用
- [x] 会話、ヒント、科目選択、明示的な正答開示、学習モード
- [ ] 対象Ubuntu実機でWayland/X11の範囲撮影ショートカットを検証

### Phase 2 — 指導品質

- [x] 科目A/Bの指導ルールを分離
- [x] ヒント段階と正答開示制御の基本機能
- [x] 認識内容を画面上で確認
- [ ] OCR内容の手動訂正と問題内容の更新
- [ ] 複数の過去問を使った指導品質検証

### Phase 3 — 継続学習

- [x] SQLiteに問題情報と会話を保存
- [ ] 理解度・苦手分野の集計
- [ ] 復習・再出題機能
- [ ] IPA公開資料との照合

マイルストーンはコードの機能範囲に合わせて更新します。チェック済み項目は画面とAPIに実装済み、未チェック項目は未実装または実機検証待ちです。

## セキュリティとデータ

- 既定で `127.0.0.1` のみで待ち受けます。LANへ公開する場合は認証・TLS・ファイアウォールを追加してください。
- OpenAI APIキーは `.env` にのみ保存し、ブラウザーへ送信しません。
- 自動送信されるのは、エージェントで明示的に撮影操作した範囲だけです。デスクトップ全体を監視・録画しません。
- AI読解のため、問題画像と質問内容はOpenAI APIへ送信されます。教材画像の利用条件を確認してください。
- 問題情報と会話はSQLiteに保存します。新しい撮影画像はサーバーのRAMだけに一時保持し、最大12枚または64MBで古い画像から破棄します。サーバー再起動後や画像破棄後は再撮影が必要です。
- 以前の版で `data/images/` に保存された画像は自動削除しません。必要に応じて画面から該当する履歴を削除してください。

## 開発メモ

```bash
.venv/bin/uvicorn app.main:app --reload
```

依存関係の定義は `pyproject.toml` を使用します。データファイル、仮想環境、`.env` は `.gitignore` で管理対象から除外しています。
