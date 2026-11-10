#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_path="$project_dir/.venv/bin/python"
unit_dir="$HOME/.config/systemd/user"
unit_path="$unit_dir/ap-tutor-agent.service"

if [[ ! -x "$python_path" ]]; then
  echo "仮想環境がありません。先にREADMEのセットアップを完了してください。" >&2
  exit 1
fi

mkdir -p "$unit_dir"
cat > "$unit_path" <<UNIT
[Unit]
Description=AP Tutor screenshot auto uploader
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$project_dir
ExecStart=$python_path -m ubuntu_agent.main --watch
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
session_env=()
for variable in DISPLAY XAUTHORITY XDG_SESSION_TYPE WAYLAND_DISPLAY XDG_RUNTIME_DIR; do
  if [[ -n "${!variable:-}" ]]; then
    session_env+=("$variable")
  fi
done
if ((${#session_env[@]})); then
  systemctl --user import-environment "${session_env[@]}"
fi
systemctl --user enable --now ap-tutor-agent.service
systemctl --user restart ap-tutor-agent.service
echo "起動設定を登録しました。状態確認: systemctl --user status ap-tutor-agent.service"
