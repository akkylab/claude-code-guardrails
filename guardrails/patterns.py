"""止める対象の定義を1箇所へ集めたモジュール。

各フックがそれぞれパターンを持つと、新しい危険を見つけたときにどこへ足せば
よいか分からなくなります。定義はここだけに置き、フックは判定の流れだけを
持ちます。

## 3種類の定義があります

| 定義 | 何を見るか | 例 |
|---|---|---|
| `SECRET_FILE_NAMES` | ファイルの名前 | `.env`・`.env.production`・`id_rsa` |
| `SECRET_DUMP_COMMANDS` | コマンドの形そのもの | `gh auth token`・`stripe config --list` |
| `_DESTRUCTIVE_RULES` | 取り返しのつかない操作 | `rm -rf`・`git reset --hard` |

2番目が要るのは、ファイルのパスを引数に取らないのに秘密を吐くコマンドがある
ためです。パスだけを見張っていても、これらは素通りします。
"""

from __future__ import annotations

import re
from typing import Final

# 秘密が入っているとみなすファイルの名前。完全一致ではなく、ファイル名が
# これらのいずれかで始まるかどうかで判定します。`.env.production` のような
# 派生を1つずつ書き並べずに済ませるためです。
SECRET_FILE_PREFIXES: Final[tuple[str, ...]] = (
    ".env",
    ".envrc",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".pgpass",
)

# 秘密が入っているとみなすファイル名。こちらは完全一致で見ます。
SECRET_FILE_NAMES: Final[tuple[str, ...]] = (
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "terraform.tfvars",
)

# 引数にパスを取らないのに、秘密を標準出力へ吐くコマンド。
# 値は「なぜ危ないか」で、止めるときに利用者へそのまま見せます。
SECRET_DUMP_COMMANDS: Final[dict[str, str]] = {
    "gh auth token": "GitHubのアクセストークンがそのまま出ます",
    "stripe config --list": "認証済みの全プロジェクトのAPIキーが出ます（本番のキーを含みます）",
    "security find-generic-password": "macOSキーチェーンのパスワードが出ます",
    "claude mcp list": "MCPサーバーへ渡したAPIキーが出ます",
    "codex mcp list": "MCPサーバーへ渡したAPIキーが出ます",
    "codex mcp get": "MCPサーバーへ渡したAPIキーが出ます",
    "aws configure get": "AWSのアクセスキーが出ます",
    "op item get": "1Passwordに保管した項目の中身が出ます",
    "vault read": "Vaultに保管した秘密が出ます",
    "kubectl get secret": "Kubernetesのシークレットが出ます",
    "heroku config": "Herokuの環境変数がすべて出ます",
    "doctl auth init": "DigitalOceanのトークンが出ます",
    "firebase login:ci": "Firebaseのデプロイ用トークンが出ます",
}

# 引数なしで実行すると環境変数を全部出すコマンド。引数が付いていれば
# 特定の変数を見るだけなので、素通しします。
BARE_ENV_DUMP_COMMANDS: Final[tuple[str, ...]] = ("env", "printenv", "set")

# 取り返しのつかない操作。`(正規表現, 何が起きるか, 代わりにどうするか)` の形で
# 持ちます。3つ目を必ず書くのは、止めるだけでは利用者が次に進めないためです。
# 正規表現は `destructive_rules()` が組み立てて返します。
_DESTRUCTIVE_RULES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/(\s|$)",
        "ルートディレクトリを再帰的に消そうとしています",
        "消したい場所を具体的に指定してください",
    ),
    (
        r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+~(/\s*)?(\s|$)",
        "ホームディレクトリを丸ごと消そうとしています",
        "消したい場所を具体的に指定してください",
    ),
    (
        r"\bgit\s+reset\s+--hard\b",
        "コミットしていない変更が、元に戻せない形で消えます",
        "先に `git stash` で退避するか、`git status` で失うものを確認してください",
    ),
    (
        r"\bgit\s+clean\s+-[a-zA-Z]*f",
        "追跡していないファイルが、元に戻せない形で消えます",
        "先に `git clean -n` を実行して、何が消えるかを確認してください",
    ),
    (
        r"\bgit\s+push\s+.*--force(?!-with-lease)\b",
        "リモートの履歴を上書きします。他の人のコミットが消えることがあります",
        "`--force-with-lease` を使ってください。他の人の更新があれば止まります",
    ),
    (
        r"\bgit\s+branch\s+-D\b",
        "マージしていないブランチを、確認なしで消します",
        "`git branch -d` を使ってください。未マージなら止まります",
    ),
    # SQLは大文字小文字を区別しないので、この2つだけ `(?i)` を付けます。
    # シェルのコマンドは区別するため、全体へ一律に無視をかけてはいけません。
    # `git branch -D`（未マージでも消す）と `-d`（安全）を取り違えます。
    (
        r"(?i)\bDROP\s+(TABLE|DATABASE|SCHEMA)\b",
        "テーブルやデータベースを丸ごと消します",
        "本当に消す場合は、先にバックアップを取ってください",
    ),
    (
        r"(?i)\bTRUNCATE\s+TABLE\b",
        "テーブルの中身を全部消します",
        "本当に消す場合は、先にバックアップを取ってください",
    ),
    (
        r"\bchmod\s+(-[a-zA-Z]+\s+)*777\b",
        "誰でも書き換えられる権限を与えようとしています",
        "必要な権限だけを与えてください（多くの場合は755か644です）",
    ),
    (
        r"\bmkfs(\.[a-z0-9]+)?\b",
        "ディスクを初期化します。中身は戻りません",
        "対象のデバイスを間違えていないか、必ず確認してください",
    ),
    (
        r"\bdd\s+.*\bof=/dev/",
        "デバイスへ直接書き込みます。中身は戻りません",
        "書き込み先のデバイスを間違えていないか、必ず確認してください",
    ),
)


def destructive_rules() -> tuple[tuple[re.Pattern[str], str, str], ...]:
    """取り返しのつかない操作の判定規則を返します。

    正規表現は毎回組み立てず、この関数を通して受け取ってください。

    Returns:
        `(正規表現, 何が起きるか, 代わりにどうするか)` の組の並び。

    """
    # 大文字小文字の無視を全体へかけません。シェルのコマンドは区別するため、
    # 無視をかけると `git branch -D` の規則が安全な `-d` まで拾ってしまいます。
    # 区別が要らないSQLの規則だけ、パターンの先頭に `(?i)` を書いています。
    return tuple(
        (re.compile(pattern), consequence, alternative)
        for pattern, consequence, alternative in _DESTRUCTIVE_RULES
    )


def is_secret_file(name: str) -> bool:
    """そのファイル名が、秘密を含むとみなす対象かを判定します。

    Args:
        name: ファイル名。ディレクトリを含まない、末尾の部分だけを渡します。

    Returns:
        秘密を含むとみなす場合はTrue。

    """
    if not name:
        return False

    lowered = name.lower()

    if lowered in SECRET_FILE_NAMES:
        return True

    # `.env.example` と `.env.template` は、値ではなく雛形なので通します。
    # これを止めると、新しい利用者が設定の書き方を読めなくなります。
    if lowered.startswith(".env") and lowered.endswith((".example", ".template", ".sample")):
        return False

    return lowered.startswith(SECRET_FILE_PREFIXES)


def secret_dump_reason(command: str) -> str:
    """そのコマンドが秘密を吐くかを判定し、理由を返します。

    Args:
        command: 実行しようとしているコマンドの全文。

    Returns:
        秘密を吐く場合はその理由。そうでなければ空文字列。

    """
    if not command:
        return ""

    normalized = " ".join(command.split()).lower()

    for prefix, reason in SECRET_DUMP_COMMANDS.items():
        # パイプやセミコロンの後ろに現れる場合も拾います。`ls | gh auth token`
        # のような書き方で素通りさせないためです。
        appears_after_separator = f"| {prefix}" in normalized or f"; {prefix}" in normalized
        if normalized.startswith(prefix) or appears_after_separator:
            return reason

    if normalized in BARE_ENV_DUMP_COMMANDS:
        return "環境変数の一覧がすべて出ます"

    return ""
