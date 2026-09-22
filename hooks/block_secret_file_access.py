#!/usr/bin/env python3
"""秘密が入ったファイルの読み書きを止めるフック。

`.env` のようなファイルは、たいていGit管理から外れています。中身を失うと復元
できませんし、読めば値がそのまま会話へ入ります。ここで機械的に止めます。

## 2つの入口を見ます

| 入口 | 何を見るか | 例 |
|---|---|---|
| ファイル系のツール | `file_path` の末尾 | Read・Edit・Write・NotebookEdit |
| Bash | コマンドに現れるパス | `cat .env`・`echo x >> .env` |

Bashを見るのは、ファイル系のツールだけ見張っても `cat .env` で素通りする
ためです。

## 雛形は通します

`.env.example`・`.env.template`・`.env.sample` は値ではなく書き方の見本です。
これを止めると、新しい利用者が設定の書き方を読めなくなります。

## 設定

環境変数 `GUARDRAILS_ALLOW_SECRET_FILES` に `1` を入れると、このフックは何も
しません。CIなど、意図して `.env` を生成する場面のための逃げ道です。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# このファイルは絶対パスで起動されるため、パッケージの置き場を自分で通します。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardrails import hook_io, patterns

HOOK_NAME = "block-secret-file-access"

# パスを引数に取るツール。`file_path` の欄を見ます。
FILE_TOOLS = ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit")

# コマンドの中からパスらしき文字列を拾う正規表現。引用符の有無を問わず、
# 空白・パイプ・リダイレクトで区切られた単位を取り出します。
_TOKEN_PATTERN = re.compile(r"""[^\s'"|;&<>()]+""")

MESSAGE = """\
秘密が入ったファイルを操作しようとしています。

  対象: {path}

このフックは次の理由で止めています。

  1. `.env` はGit管理から外れているため、失うと復元できません
  2. 読み取ると、APIキーやパスワードがそのまま会話のログへ残ります

書き方を変えて再試行しないでください。止まったこと自体が正しい動作です。

値の確認や書き換えが要る場合は、あなた自身のエディタで開いてください。

  code .env

雛形（`.env.example`）は止めていません。設定の書き方はそちらで読めます。
"""


def _is_blocked_path(raw: str) -> bool:
    """そのパス文字列が、止める対象かを判定します。

    Args:
        raw: パスらしき文字列。空文字列でも構いません。

    Returns:
        止める対象ならTrue。

    """
    if not raw:
        return False

    # 末尾のスラッシュを落としてから名前を取り出します。`.env/` のような
    # 書き方でも同じ判定になるようにするためです。
    name = Path(raw.rstrip("/")).name
    return patterns.is_secret_file(name)


def _blocked_path_in_command(command: str) -> str:
    """コマンドの中から、止める対象のパスを1つ探します。

    Args:
        command: 実行しようとしているコマンドの全文。

    Returns:
        見つかったパス。無ければ空文字列。

    """
    if not command:
        return ""

    tokens: list[str] = _TOKEN_PATTERN.findall(command)
    for token in tokens:
        if _is_blocked_path(token):
            return token

    return ""


def main() -> None:
    """フックの入口。"""
    if os.environ.get("GUARDRAILS_ALLOW_SECRET_FILES") == "1":
        hook_io.allow()

    logger = hook_io.setup_logging(HOOK_NAME)
    payload = hook_io.read_hook_input()

    name = hook_io.tool_name(payload)
    arguments = hook_io.tool_input(payload)

    if name in FILE_TOOLS:
        target = hook_io.string_field(arguments, "file_path")
        if _is_blocked_path(target):
            logger.info("止めました: ツール=%s", name)
            hook_io.block(MESSAGE.format(path=target))

    if name == "Bash":
        command = hook_io.string_field(arguments, "command")
        found = _blocked_path_in_command(command)
        if found:
            logger.info("止めました: ツール=Bash")
            hook_io.block(MESSAGE.format(path=found))

    hook_io.allow()


if __name__ == "__main__":
    main()
