# 実装方針

## 目的

GCP e2-micro上のMinecraft Bedrock Dedicated ServerをOCI Ampere A1へ移し、
PCやSwitchなどのBedrockクライアントから継続して利用できるようにします。

## 採用構成

```text
Bedrock client
    |-- NetherNet signaling: TCP/19132
    |-- NetherNet gameplay: UDP/32000-32015
    v
OCI VCN / NSG
    |
    v
systemd -> Box64 ARM64 DynaRec -> official BDS x86_64 -> Bedrock world
```

公式Linux BDSはARM64バイナリを提供していないため、BDSプロセスだけをBox64で
実行します。ワールド、systemd、バックアップ、疎通確認はエミュレーションに
依存しません。

## この方式を選ぶ理由

- 公式BDSを継続するため、既存ワールドの変換が不要
- JavaサーバーとGeyserへの変換より、Bedrock固有データの互換性リスクが低い
- コンテナ全体をx86_64エミュレーションするより対象範囲が小さい
- Box64とBDSを個別に固定・更新・ロールバックできる

Box64は完全なネイティブ実行ではありません。x86_64命令をARM64へ動的変換します。
ただし周辺サービスはネイティブのままであり、互換性と性能のバランスがよい構成です。

## 配置

| パス | 用途 |
| --- | --- |
| `/usr/local/bin/box64` | Box64実行ファイル |
| `/opt/minecraft/server` | BDS、設定、ワールド |
| `/var/cache/minecraft-box64` | Box64 DynaCache |
| `/etc/systemd/system/minecraft.service` | サービス定義 |

Minecraftは専用の `minecraft` ユーザーで動かし、rootでは実行しません。

## バージョン管理

Box64と初期導入用BDSのバージョンは `config/versions.env` に固定します。導入時に
取得したBox64のGit commitとBDSアーカイブのSHA-256を `/opt/minecraft` 以下の
manifestへ記録します。

BDSは `minecraft-update.timer` で日次更新します。更新処理はMinecraft公式の
ダウンロードリンクAPIからLinux BDSの `minecraft.net` URLを取得します。旧実装との
互換性のためMinecraft Wikiの抽出もフォールバックとして残します。ダウンロードと
展開を稼働中に済ませ、停止後にワールドと設定を新しいディレクトリへ移します。
新バージョンが起動できない場合は直前のディレクトリへ自動的に戻します。適用した
バージョン、URL、SHA-256は毎回manifestへ記録します。Box64の更新はBDSと分離して
手動検証します。

## ネットワーク

- Bedrock NetherNet signaling: `19132/TCP`
- Bedrock NetherNet gameplay: `32000-32015/UDP`
- Let's Encrypt認証とHTTPS転送: `80/TCP`
- 監視画面: `443/TCP` -> nginx -> `127.0.0.1:8080`
- BDSからMinecraft signaling serviceへのアウトバウンド通信
- 監視サービスからntfy.shへのアウトバウンドHTTPS
- SSH: 管理元IPからのみ許可する

OCIのVCN Security List/NSGとOSファイアウォールは別レイヤーです。両方を確認します。
現行BDSは `transport=nethernet` のみプレイヤー接続をサポートします。旧版のRakNet用
`19132/UDP` pingは現行BDSの疎通確認には使用しません。BDS 1.26.52.3の
`server-udp-ports` parserは範囲mappingとカンマ区切りを正しく処理しないため、
パブリックIPと各UDPポートのmappingを1行ずつ設定します。
Switchから任意サーバーへ接続する方法はクライアント側の制約を受けるため、現行環境と
同じ接続方式で最終確認します。

## 監視

`minecraft-monitor.service` はsystemd journalの参加・退出行と `/proc` の負荷情報を
SQLiteへ保存します。journal側で対象行を絞り、初回同期がMinecraft本体を圧迫しない
ようにします。Web APIはlocalhost限定で、外部公開、TLS、Basic認証はnginxが担当します。
ntfy送信は別スレッドで行い、通知先障害が収集とWeb応答を止めない構成です。

## ロールバック

移行後にPCとSwitchから接続し、ワールド、持ち物、権限を確認するまではGCP VMを
削除しません。問題があればOCI側を停止し、GCP側を再起動します。同一ワールドを
両方で同時起動しないことが重要です。

## 運用監視

監視サービスはjournal全量走査を避け、参加関連ログ、systemd状態、プロセス資源を
定期取得します。証明書はcertbot timer、BDSはminecraft-update timerで更新します。
どちらも更新後のサービス状態とjournalを確認します。
