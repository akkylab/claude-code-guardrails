#!/usr/bin/env python3
"""フックをClaude Codeの設定へ組み込むスクリプト。

`~/.claude/settings.json` の `hooks` へ、このリポジトリのフックを追加します。

    ./install.py              追加する
    ./install.py --dry-run    何が変わるかだけ見る
    ./install.py --uninstall  追加したものを外す

## 既にある設定を壊しません

設定ファイルには、利用者が自分で入れたフックが既に入っていることがあります。
このスクリプトは、追加するフックと同じパスの項目だけを入れ替え、それ以外には
手を触れません。

書き換える前に、必ず `settings.json.guardrails-backup` へ退避します。

## 判定に使う手掛かり

追加した項目かどうかは、コマンド文字列がこのリポジトリの `hooks/` を指して
いるかで見分けます。設定ファイルへ目印のコメントを書く方法は採りません。
JSONにはコメントが書けないためです。
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

# 組み込むフックと、それをどのツールの前に走らせるか。
# `matcher` はClaude Codeがツール名を照合する正規表現です。
HOOKS: tuple[tuple[str, str], ...] = (
    ("block_secret_file_access.py", "Read|Edit|Write|NotebookEdit|MultiEdit|Bash"),
    ("block_secret_dump_commands.py", "Bash"),
    ("block_destructive_commands.py", "Bash"),
)

EVENT = "PreToolUse"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
BACKUP_SUFFIX = ".guardrails-backup"


def hooks_dir() -> Path:
    """このリポジトリのフックの置き場を返します。

    Returns:
        `hooks/` の絶対パス。

    """
    return Path(__file__).resolve().parent / "hooks"


def load_settings(path: Path) -> dict[str, Any]:
    """設定ファイルを読みます。

    Args:
        path: `settings.json` のパス。

    Returns:
        設定の中身。ファイルが無ければ空の辞書。

    Raises:
        SystemExit: JSONとして読めない場合。壊れた設定を上書きしないため、
            ここで止めます。

    """
    if not path.exists():
        return {}

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(
            f"設定ファイルをJSONとして読めません: {path}\n  {error}\n"
            "壊れた設定を上書きしないため、ここで止めます。先に直してください。",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    except OSError as error:
        print(f"設定ファイルを読めません: {path}\n  {error}", file=sys.stderr)
        raise SystemExit(1) from error

    return parsed if isinstance(parsed, dict) else {}


def is_ours(entry: dict[str, Any], directory: Path) -> bool:
    """その項目が、このリポジトリのフックかを判定します。

    Args:
        entry: `hooks` の中の1項目。
        directory: このリポジトリの `hooks/` の絶対パス。

    Returns:
        このリポジトリのフックならTrue。

    """
    commands = entry.get("hooks")
    if not isinstance(commands, list):
        return False

    return any(
        isinstance(item, dict) and str(directory) in str(item.get("command", ""))
        for item in commands
    )


def build_entries(directory: Path) -> list[dict[str, Any]]:
    """組み込む項目を組み立てます。

    Args:
        directory: このリポジトリの `hooks/` の絶対パス。

    Returns:
        `settings.json` へ入れる項目の並び。

    """
    return [
        {
            "matcher": matcher,
            "hooks": [{"type": "command", "command": str(directory / filename)}],
        }
        for filename, matcher in HOOKS
    ]


def apply(settings: dict[str, Any], directory: Path, *, uninstall: bool) -> dict[str, Any]:
    """設定へフックを足す、または外します。

    Args:
        settings: いまの設定。
        directory: このリポジトリの `hooks/` の絶対パス。
        uninstall: Trueなら外します。

    Returns:
        書き換えたあとの設定。元の辞書は変更しません。

    """
    # 呼び出し元の辞書を書き換えないよう、深い複製を作ります。JSONへ直して
    # 読み直す方法だと型が失われるので、copyを使います。
    updated: dict[str, Any] = copy.deepcopy(settings)

    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        updated["hooks"] = hooks

    existing = hooks.get(EVENT)
    if not isinstance(existing, list):
        existing = []

    # このリポジトリの項目だけを取り除きます。利用者が自分で入れたものは残します。
    kept = [
        entry
        for entry in existing
        if not (isinstance(entry, dict) and is_ours(entry, directory))
    ]

    hooks[EVENT] = kept if uninstall else kept + build_entries(directory)

    # 空の欄を残すと、設定ファイルが読みにくくなります。
    if not hooks[EVENT]:
        del hooks[EVENT]
    if not hooks:
        del updated["hooks"]

    return updated


def save(path: Path, settings: dict[str, Any]) -> None:
    """設定を書き出します。退避を取ってから上書きします。

    Args:
        path: `settings.json` のパス。
        settings: 書き出す中身。

    Raises:
        SystemExit: 書き込めない場合。

    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            backup = path.with_suffix(path.suffix + BACKUP_SUFFIX)
            shutil.copy2(path, backup)
            print(f"退避しました: {backup}")

        path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"設定ファイルを書けません: {path}\n  {error}", file=sys.stderr)
        raise SystemExit(1) from error


def main() -> None:
    """スクリプトの入口。"""
    parser = argparse.ArgumentParser(
        description="Claude Codeの設定へガードレールのフックを組み込みます。",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="書き換えず、結果だけを表示します",
    )
    parser.add_argument(
        "--uninstall", action="store_true", help="組み込んだフックを外します",
    )
    args = parser.parse_args()

    directory = hooks_dir()
    if not directory.is_dir():
        print(f"フックの置き場が見つかりません: {directory}", file=sys.stderr)
        raise SystemExit(1)

    settings = load_settings(SETTINGS_PATH)
    updated = apply(settings, directory, uninstall=args.uninstall)

    if settings == updated:
        print("設定はすでに望む状態です。何も変えていません。")
        return

    if args.dry_run:
        print("--dry-runのため書き換えていません。書き換えると次になります。\n")
        print(json.dumps(updated.get("hooks", {}), ensure_ascii=False, indent=2))
        return

    save(SETTINGS_PATH, updated)

    action = "外しました" if args.uninstall else "組み込みました"
    print(f"{action}: {SETTINGS_PATH}")
    print("Claude Codeを起動し直すと反映されます。")


if __name__ == "__main__":
    main()
