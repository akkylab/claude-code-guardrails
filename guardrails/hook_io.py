"""フックの入口と出口を受け持つ共通部品。

`hooks/` の各フックは、起動されるたびに同じ2つを行います。標準入力からJSONを
読むことと、判定の結果をハーネスへ返すことです。どちらも判定の中身とは無関係
なので、ここに集めています。

## ハーネスとの約束

Claude Codeは、フックを起動して標準入力にJSONを流し込み、終了コードで結果を
判断します。約束は次の3つだけです。

| 終了コード | 意味 | 標準エラーの扱い |
|---|---|---|
| 0 | 通してよい | 表示しない |
| 2 | 止める | Claude Codeへ理由として渡る |
| その他 | フック自体の失敗 | 警告として表示され、操作は通る |

止めたい理由をClaude Codeへ伝えられるのは終了コード2のときだけです。1や3で
返すと、理由が伝わらないまま操作が通ります。

## 入力の中身をログに書きません

このモジュールは、受け取ったJSONをログへ書ける形そのものを持ちません。
`read_hook_input` はロガーを引数に取らないので、呼び出し側が指示しても中身は
残りません。

PreToolUseが受け取るのはコマンド文字列なので、次のような実行をすれば秘密が
確実に記録されてしまいます。

    curl -H "Authorization: Bearer <トークン>" https://api.example.com

記録が要る場面では、各フックが値を含まない形（ツール名・件数・判定結果など）
で自分のロガーへ書いてください。

## ログが書けなくても判定は止めません

ログは後から様子を見るためのものです。書けないことを理由にツールの実行を
止めると、利用者の作業が巻き添えになります。置き場を作れないときは標準エラー
へ一言出して、何もしないロガーで先へ進みます。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# 操作を止めるときの終了コード。ハーネスの仕様で、標準エラーへ書いた理由が
# Claude Codeへ伝わるのはこの値のときだけです。
BLOCK_EXIT_CODE = 2

# 操作を通すときの終了コード。
ALLOW_EXIT_CODE = 0

# ログを捨てる大きさ。これを超えたら中身を捨てて新しく書き始めます。
# RotatingFileHandlerを使わないのは、世代を残す必要がなく、フックが1回の起動で
# 数行しか書かないためです。世代管理を持つほうが仕組みとして重くなります。
MAX_LOG_SIZE_BYTES = 1_000_000

# ログファイルの権限。所有者だけが読み書きできる状態にします。
# フックのログには判定対象のコマンドが載るため、他の利用者から読めてはいけません。
LOG_FILE_MODE = 0o600

LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def log_dir() -> Path:
    """ログの置き場を返します。

    環境変数 `GUARDRAILS_LOG_DIR` で差し替えられます。テストが本物のホーム
    ディレクトリを汚さないために要ります。

    Returns:
        ログを置くディレクトリ。まだ存在しないこともあります。

    """
    override = os.environ.get("GUARDRAILS_LOG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "logs"


def _attach_file_handler(logger: logging.Logger, name: str) -> None:
    """ロガーにファイル出力を取り付けます。

    Args:
        logger: 取り付け先のロガー。
        name: フックの名前。ログのファイル名になります。

    Raises:
        OSError: 置き場を作れない、または書き込めない場合。

    """
    directory = log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.log"

    if path.exists() and path.stat().st_size > MAX_LOG_SIZE_BYTES:
        path.unlink()

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    logger.addHandler(handler)

    path.chmod(LOG_FILE_MODE)


def setup_logging(name: str) -> logging.Logger:
    """このフック用のロガーを用意します。

    置き場を作れなかった場合も、同じ名前の何もしないロガーを返します。
    呼び出し側は成否を気にせず使って構いません。

    Args:
        name: フックの名前。ファイル名（拡張子なし）と揃えてください。

    Returns:
        設定済みのロガー。

    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 同じプロセスで2回呼ばれても、ハンドラが二重に付かないようにします。
    if logger.handlers:
        return logger

    try:
        _attach_file_handler(logger, name)
    except OSError as error:
        # ログが書けないだけで利用者の作業を止めません。
        logger.addHandler(logging.NullHandler())
        print(f"guardrails: ログを書けません（{error}）", file=sys.stderr)

    return logger


def read_hook_input() -> dict[str, Any]:
    """標準入力からフックへの入力JSONを読みます。

    JSONとして読めない場合は空の辞書を返します。入力が壊れていることを理由に
    利用者の操作を止めないためです。判定に要る欄が無ければ、各フックが素通し
    します。

    Returns:
        フックへの入力。読めなければ空の辞書。

    """
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return {}

    if not raw.strip():
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    """入力から `tool_input` を取り出します。

    Args:
        payload: `read_hook_input` が返した辞書。

    Returns:
        ツールへ渡された引数。無ければ空の辞書。

    """
    value = payload.get("tool_input")
    return value if isinstance(value, dict) else {}


def tool_name(payload: dict[str, Any]) -> str:
    """入力からツール名を取り出します。

    Args:
        payload: `read_hook_input` が返した辞書。

    Returns:
        ツール名。無ければ空文字列。

    """
    value = payload.get("tool_name")
    return value if isinstance(value, str) else ""


def string_field(source: dict[str, Any], key: str) -> str:
    """辞書から文字列の欄を取り出します。

    ツールへの引数は外から来るので、期待した型で入っている保証がありません。
    文字列でなければ空文字列として扱い、各フックが素通しできるようにします。

    Args:
        source: 取り出し元の辞書。
        key: 欄の名前。

    Returns:
        欄の値。文字列でなければ空文字列。

    """
    value = source.get(key)
    return value if isinstance(value, str) else ""


def block(reason: str) -> None:
    """操作を止めて、理由をClaude Codeへ返します。

    この関数は戻りません。

    Args:
        reason: なぜ止めたのかと、代わりに何をすればよいか。Claude Codeは
            この文章を読んで次の手を決めるので、直し方まで書いてください。

    """
    print(reason, file=sys.stderr)
    sys.exit(BLOCK_EXIT_CODE)


def allow() -> None:
    """操作を通します。

    この関数は戻りません。

    """
    sys.exit(ALLOW_EXIT_CODE)
