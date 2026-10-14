# 移行手順

## 0. 前提確認

- A1が `aarch64` であること
- A1に2コア、約12GB RAM、十分なディスク空きがあること
- GCPへSSH接続できること
- OCIで `19132/TCP` と `32000-32015/UDP` を許可できること
- `config/versions.env` のBDSバージョンが移行元と一致すること

## 1. A1で空ワールドを検証

```bash
sudo ./scripts/install-box64.sh
sudo ACCEPT_MINECRAFT_EULA=TRUE PUBLIC_IP=203.0.113.10 \
  SERVER_NAME='Example Bedrock Server' ./scripts/install-bedrock.sh
sudo ./scripts/install-service.sh
sudo systemctl start minecraft.service
./tests/smoke-test.sh
```

この時点ではGCPを停止しません。A1のローカルTCP待受とsystemdログを確認します。
必要ならOCI側で `19132/TCP` と `32000-32015/UDP` を開け、外部クライアントから
空ワールドへ接続します。

## 2. GCPで整合性バックアップを取得

GCP側のリポジトリへ `scripts/source-backup.sh` を配置するか、このリポジトリから
SSH経由で実行します。スクリプトはBDSを停止し、取得後に元のサービスを再起動します。

```bash
sudo CONFIRM_MINECRAFT_DOWNTIME=YES \
  ./scripts/source-backup.sh /opt/minecraft "$HOME/backups"
```

生成された `.tar.gz` と `.sha256` をA1へ転送し、ハッシュを照合します。

## 3. A1へ復元

```bash
sudo ./scripts/restore-backup.sh backups/minecraft-data-YYYYmmddTHHMMSSZ.tar.gz
./tests/smoke-test.sh
```

復元スクリプトはA1側サービスを停止し、既存データを `/opt/minecraft/rollback` に
退避してから展開します。成功後にサービスを起動します。

## 4. 外部接続を確認

1. OCI VCN Security ListまたはNSGで `19132/TCP` と `32000-32015/UDP` を許可する
2. OSファイアウォールを使用している場合も同じTCP・UDPポートを許可する
3. PCからA1のパブリックIPへ接続する
4. Switchから現行と同じ方式で接続する
5. ワールド、持ち物、権限、時刻、ゲームモードを確認する
6. `journalctl -u minecraft.service` に継続的なエラーがないことを確認する

BDS 1.26.52.3では `server-udp-ports` の範囲mappingが正しく解析されません。
`install-bedrock.sh` が `PUBLIC_IP` から生成する単一ポートmappingを範囲指定や
カンマ区切りへ変更しないでください。パブリックIPまたはUDP範囲を変える場合は、
BDS設定、OCI、OSファイアウォールを同時に更新します。

## 5. 監視を導入

OCI VCN Security ListまたはNSGとOSファイアウォールで `80/TCP` と `443/TCP` を
許可します。
その後、Basic認証の初期パスワードを指定して導入します。

```bash
sudo MONITOR_USER=dashboard-admin MONITOR_PASSWORD='temporary-password' \
  PUBLIC_IP=203.0.113.10 ./scripts/install-monitor.sh
```

DuckDNSをA1の公開IPへ向け、Let's Encrypt証明書へ切り替えます。

```bash
sudo ./scripts/enable-monitor-domain.sh example.duckdns.org
```

`https://example.duckdns.org/minecraft/` の認証、表示、参加履歴を確認し、
`/etc/minecraft-monitor.env` のntfy URLを購読します。実際の参加・退出で通知が届く
ことを確認します。`sudo certbot renew --dry-run` で証明書更新も検証します。

## 6. 切り替えとロールバック条件

確認が完了するまではGCP側を停止状態で保持します。次のいずれかがあれば切り戻します。

- ワールドやプレイヤーデータに欠損がある
- Box64またはBDSが繰り返し異常終了する
- Switchから接続できない
- 通常プレイでCPU使用率や遅延が許容できない

切り戻す場合はA1を先に停止し、その後GCPを起動します。

## 7. 自動更新を確認

移行確認後、日次timerと次回実行時刻を確認します。

```bash
systemctl status minecraft-update.timer
systemctl list-timers minecraft-update.timer
```

手動更新は `sudo systemctl start minecraft-update.service` で実行できます。更新履歴は
`journalctl -u minecraft-update.service`、適用バージョンとSHA-256は
`/opt/minecraft/install-manifest.txt` で確認します。
