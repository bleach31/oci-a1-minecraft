# Minecraft Bedrock on OCI A1

OCI Ampere A1 (`aarch64`) で公式 Minecraft Bedrock Dedicated Server の
x86_64 バイナリを Box64 経由で運用し、既存GCPサーバーからワールドを移行するための
構成管理リポジトリです。

## 方針

- Minecraft本体は公式BDSを使用し、Box64のARM64 DynaRecで実行する
- 初期構築用のBox64とBDSを `config/versions.env` で固定する
- 稼働後のBDSは日次timerで公式最新版へ安全に追従する
- ワールド形式は変換せず、停止中に取得したバックアップを移送する
- systemd、バックアップ、検証処理はARM64ネイティブで動かす
- 移行完了までGCP側をロールバック先として保持する

設計判断は [docs/architecture.md](docs/architecture.md)、実際の移行手順は
[docs/migration-runbook.md](docs/migration-runbook.md) を参照してください。

## 構成

```text
config/                 公開可能な標準設定とnginxテンプレート
docs/                   設計と移行手順
monitor/                監視APIとWeb UI
scripts/                導入、更新、バックアップ、復元
systemd/                Minecraftサービス定義
tests/                  unit test、構文検査、実機smoke test
```

`config/server.properties`、`allowlist.json`、`permissions.json` は公開用の標準設定です。
現行BDSで必須の `transport=nethernet` は明示しています。公開IP、サーバー名、権限は
導入時の環境変数または外部ファイルで指定し、リポジトリへ個人設定を保存しません。

## コマンドの役割

| 目的 | コマンド |
| --- | --- |
| Box64、BDS、systemdの初期導入 | `scripts/install-box64.sh`、`install-bedrock.sh`、`install-service.sh` |
| 監視画面とHTTPSの導入 | `scripts/install-monitor.sh`、`enable-monitor-domain.sh` |
| 移行元のバックアップと移行先への復元 | `scripts/source-backup.sh`、`restore-backup.sh` |
| BDSの安全な更新 | `scripts/update-bedrock.sh`（systemdから実行） |
| unit testと構文検査 | `tests/check.sh` |
| 稼働中BDSのsmoke test | `tests/smoke-test.sh` |

## セットアップ概要

以下はA1上で、このリポジトリのルートから実行します。

```bash
sudo ./scripts/install-box64.sh
sudo ACCEPT_MINECRAFT_EULA=TRUE PUBLIC_IP=203.0.113.10 \
	SERVER_NAME='Example Bedrock Server' ./scripts/install-bedrock.sh
sudo ./scripts/install-service.sh
sudo systemctl start minecraft.service
./tests/smoke-test.sh
```

個別のallowlistやoperator権限を初期導入へ渡す場合は、Git管理外のファイルを指定します。

```bash
sudo ACCEPT_MINECRAFT_EULA=TRUE PUBLIC_IP=203.0.113.10 \
	ALLOWLIST_FILE=/secure/allowlist.json PERMISSIONS_FILE=/secure/permissions.json \
	./scripts/install-bedrock.sh
```

`install-service.sh` はBDS更新用の `minecraft-update.timer` も有効化します。
手動で最新版を確認・適用する場合は次を実行します。

```bash
sudo /usr/local/sbin/update-minecraft-bedrock
```

OCIのVCN Security ListまたはNSGでsignaling用の `19132/TCP` とgameplay用の
`32000-32015/UDP` を許可する作業は別途必要です。OS側のファイアウォールも
利用している場合は同じポートを許可してください。

BDS 1.26.52.3には `server-udp-ports` の範囲mappingを解析できない既知不具合
（BDS-23126）があります。`install-bedrock.sh` は `PUBLIC_IP` から各UDPポートの
単一mappingを生成します。範囲指定やカンマ区切りへまとめないでください。

## 監視

監視画面はnginxから `https://example.duckdns.org/minecraft/` のようなURLで公開します。
OCIとOSファイアウォールで `80/TCP` と `443/TCP` を許可してください。80番は
Let's Encrypt認証とHTTPSへの転送、443番は監視画面に使います。Python監視プロセス
自体は `127.0.0.1:8080` だけで待ち受けます。

初回導入または更新は、Basic認証のパスワードを環境変数で渡して実行します。

```bash
sudo MONITOR_USER=dashboard-admin MONITOR_PASSWORD='temporary-password' \
  PUBLIC_IP=203.0.113.10 ./scripts/install-monitor.sh
```

ntfyトピックは初回導入時にランダム生成され、`/etc/minecraft-monitor.env` にroot専用で
保存されます。購読URLは次で確認できます。

```bash
sudo sed -n 's/^NTFY_URL=//p' /etc/minecraft-monitor.env
```

参加・退出だけを新規イベントとして通知します。監視サービス初回起動時の過去ログ再生は
通知しません。Basic認証ファイル、秘密鍵、ntfyトピックはGitに含めません。

DuckDNSをA1の公開IPへ向けた後、Let's Encrypt証明書へ切り替えます。

```bash
sudo ./scripts/enable-monitor-domain.sh example.duckdns.org
```

証明書はcertbot timerで自動更新され、更新成功時にnginxを再読込します。

## 開発時の検証

```bash
./tests/check.sh
```

実サーバーの設定、ワールド、バックアップ、秘密情報はGitに含めません。

## 参照実装

現行GCPサーバーの構成は
[bleach31/mini-mc-server](https://github.com/bleach31/mini-mc-server) を基準にしています。
最新版の検出、公式BDS URLの抽出、ワールドと設定の保持という更新方針を継承し、
このリポジトリではA1/Box64向けの配置、systemd timer、SHA-256記録、起動失敗時の
自動ロールバックを追加しています。

## ライセンス

[MIT License](LICENSE)
