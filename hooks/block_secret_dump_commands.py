#!/usr/bin/env python3
"""秘密を標準出力へ吐くコマンドを止めるフック。

`block_secret_file_access.py` はファイルのパスを見張りますが、パスを引数に
取らないのに秘密を吐くコマンドがあります。こちらはコマンドの形そのものを
見ます。

    gh auth token              GitHubのアクセストークンが出ます
    stripe config --list       認証済みの全プロジェクトのAPIキーが出ます
    claude mcp list            MCPサーバーへ渡したAPIキーが出ます

止める対象の一覧は `guardrails/patterns.py` の `SECRET_DUMP_COMMANDS` にあり
ます。新しいコマンドを見つけたら、そこへ足してください。フックの側には判定の
流れだけを置いています。

## 設定

環境変数 `GUARDRAILS_ALLOW_SECRET_DUMP` に `1` を入れると、このフックは何も
しません。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# このファイルは絶対パスで起動されるため、パッケージの置き場を自分で通します。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardrails import hook_io, patterns

HOOK_NAME = "block-secret-dump-commands"

MESSAGE = """\
秘密を出力するコマンドを実行しようとしています。

  何が起きるか: {reason}

出力はそのまま会話のログへ残ります。一度残った値は、あとから消せません。

書き方を変えて再試行しないでください。止まったこと自体が正しい動作です。

値が要る場合は、あなた自身のターミナルで実行してください。
"""


def main() -> None:
    """フックの入口。"""
    if os.environ.get("GUARDRAILS_ALLOW_SECRET_DUMP") == "1":
        hook_io.allow()

    logger = hook_io.setup_logging(HOOK_NAME)
    payload = hook_io.read_hook_input()

    if hook_io.tool_name(payload) != "Bash":
        hook_io.allow()

    command = hook_io.string_field(hook_io.tool_input(payload), "command")
    reason = patterns.secret_dump_reason(command)

    if reason:
        # コマンドの全文は残しません。判定の結果だけを記録します。
        logger.info("止めました: 理由=%s", reason)
        hook_io.block(MESSAGE.format(reason=reason))

    hook_io.allow()


if __name__ == "__main__":
    main()
