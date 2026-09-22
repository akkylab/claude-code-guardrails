#!/usr/bin/env python3
"""取り返しのつかない操作を止めるフック。

`rm -rf /`・`git reset --hard`・`git push --force` のように、実行したあとで
元へ戻せない操作を、実行の前に止めます。

## 止めるだけでは足りません

このフックは、止める理由と一緒に「代わりに何をすればよいか」を必ず返します。
理由だけを返すと、Claude Codeは同じことを別の書き方で試そうとします。進む道を
示すほうが、結果として安全になります。

    git reset --hard   →  先に `git stash` で退避してください
    git push --force   →  `--force-with-lease` を使ってください

## 意図して実行したいとき

環境変数 `GUARDRAILS_ALLOW_DESTRUCTIVE` に `1` を入れると、このフックは何も
しません。1回だけ通したい場合は、そのコマンドの前に付けてください。

    GUARDRAILS_ALLOW_DESTRUCTIVE=1 git reset --hard

止める対象の一覧は `guardrails/patterns.py` の `_DESTRUCTIVE_RULES` にあります。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# このファイルは絶対パスで起動されるため、パッケージの置き場を自分で通します。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from guardrails import hook_io, patterns  # noqa: E402

HOOK_NAME = "block-destructive-commands"

MESSAGE = """\
元に戻せない操作を実行しようとしています。

  何が起きるか: {consequence}
  代わりにどうするか: {alternative}

本当にこの操作が要る場合は、環境変数を付けて1回だけ通してください。

  GUARDRAILS_ALLOW_DESTRUCTIVE=1 <もとのコマンド>

付ける前に、失われるものを必ず確認してください。確認せずに通すためのもの
ではありません。
"""


def main() -> None:
    """フックの入口。"""
    if os.environ.get("GUARDRAILS_ALLOW_DESTRUCTIVE") == "1":
        hook_io.allow()

    logger = hook_io.setup_logging(HOOK_NAME)
    payload = hook_io.read_hook_input()

    if hook_io.tool_name(payload) != "Bash":
        hook_io.allow()

    command = hook_io.string_field(hook_io.tool_input(payload), "command")
    if not command:
        hook_io.allow()

    for rule, consequence, alternative in patterns.destructive_rules():
        if rule.search(command):
            # コマンドの全文は残しません。どの規則に当たったかだけを記録します。
            logger.info("止めました: 理由=%s", consequence)
            hook_io.block(
                MESSAGE.format(consequence=consequence, alternative=alternative)
            )

    hook_io.allow()


if __name__ == "__main__":
    main()
