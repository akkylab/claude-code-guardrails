"""フックが「止めるべきものを止め、通すべきものを通す」ことを守ります。

フックは、足しただけでは守りになりません。止まることを示して初めて守りに
なります。ここで最も避けたい事故は2つあり、どちらも同じだけ重いものとして
扱っています。

| 事故 | 何が起きるか |
|---|---|
| 止めるべきものを通す | 秘密が会話へ残る、消したくないものが消える |
| 通すべきものを止める | 邪魔だと感じた利用者にフックを外され、何も守らなくなる |

2つ目を軽く見ないでください。誤って止めるフックは、外された時点で1つ目も
守れなくなります。そのため、止まる側と同じ数だけ通る側のテストを置いています。

各テストは、フックを実際のプロセスとして起動して終了コードを見ます。中の関数
を直接呼ぶ形にしないのは、Claude Codeが見るのがプロセスの終了コードだから
です。関数が正しくても、入口の組み立てが間違っていれば何も守れません。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"

# フックが「止めた」ことを表す終了コード。ハーネスの仕様で、標準エラーへ
# 書いた理由がClaude Codeへ伝わるのはこの値のときだけです。
BLOCKED = 2

# フックが「通した」ことを表す終了コード。
ALLOWED = 0

# 秘密が入ったファイルの名前。テストの中で組み立てて判定に使います。
DOTENV = ".env"

# 全フックの一覧。壊れた入力への強さは、どのフックでも同じだけ要ります。
ALL_HOOKS = (
    "block_secret_file_access.py",
    "block_secret_dump_commands.py",
    "block_destructive_commands.py",
)


def run_hook(
    name: str,
    payload: object,
    tmp_path: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """フックを起動して、終了コードを返します。

    Args:
        name: `hooks/` の中のファイル名。
        payload: 標準入力へ流す中身。辞書ならJSONへ直します。文字列はそのまま
            流すので、壊れた入力を試せます。
        tmp_path: ログの置き場。本物のホームディレクトリを汚さないために渡します。
        extra_env: 追加で渡す環境変数。逃げ道が効くかを試すときに使います。

    Returns:
        フックの終了コード。

    """
    environment = {"PATH": "/usr/bin:/bin", "GUARDRAILS_LOG_DIR": str(tmp_path)}
    if extra_env:
        environment.update(extra_env)

    body = payload if isinstance(payload, str) else json.dumps(payload)

    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / name)],
        input=body,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    return result.returncode


def bash(command: str) -> dict[str, object]:
    """Bashツールの入力を組み立てます。

    Args:
        command: 実行しようとしているコマンド。

    Returns:
        フックへの入力。

    """
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def read_file(path: str) -> dict[str, object]:
    """Readツールの入力を組み立てます。

    Args:
        path: 読もうとしているファイルのパス。

    Returns:
        フックへの入力。

    """
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


class Test秘密のファイルへのアクセス:
    HOOK = "block_secret_file_access.py"

    @pytest.mark.parametrize(
        "filename",
        [DOTENV, f"{DOTENV}.production", "id_rsa", "credentials.json", ".netrc"],
    )
    def test_秘密のファイルを読もうとすると止まる(
        self, filename: str, tmp_path: Path
    ) -> None:
        assert run_hook(self.HOOK, read_file(f"/somewhere/{filename}"), tmp_path) == BLOCKED

    def test_コマンド経由で秘密のファイルを読もうとしても止まる(
        self, tmp_path: Path
    ) -> None:
        assert run_hook(self.HOOK, bash(f"cat {DOTENV}"), tmp_path) == BLOCKED

    def test_書き込みも止まる(self, tmp_path: Path) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": f"/somewhere/{DOTENV}", "content": "x"},
        }
        assert run_hook(self.HOOK, payload, tmp_path) == BLOCKED

    @pytest.mark.parametrize("suffix", [".example", ".template", ".sample"])
    def test_雛形は通す(self, suffix: str, tmp_path: Path) -> None:
        payload = read_file(f"/somewhere/{DOTENV}{suffix}")
        assert run_hook(self.HOOK, payload, tmp_path) == ALLOWED

    @pytest.mark.parametrize(
        "path", ["/somewhere/README.md", "/somewhere/environment.ts", "/env/config.py"]
    )
    def test_関係のないファイルは通す(self, path: str, tmp_path: Path) -> None:
        assert run_hook(self.HOOK, read_file(path), tmp_path) == ALLOWED

    def test_環境変数で明示的に許可すると通す(self, tmp_path: Path) -> None:
        code = run_hook(
            self.HOOK,
            read_file(f"/somewhere/{DOTENV}"),
            tmp_path,
            {"GUARDRAILS_ALLOW_SECRET_FILES": "1"},
        )
        assert code == ALLOWED


class Test秘密を吐くコマンド:
    HOOK = "block_secret_dump_commands.py"

    @pytest.mark.parametrize(
        "command",
        [
            "gh auth token",
            "stripe config --list",
            "claude mcp list",
            "aws configure get aws_access_key_id",
            "printenv",
        ],
    )
    def test_秘密を出力するコマンドは止まる(self, command: str, tmp_path: Path) -> None:
        assert run_hook(self.HOOK, bash(command), tmp_path) == BLOCKED

    @pytest.mark.parametrize(
        "command",
        ["printenv PATH", "gh pr list", "git status", "stripe --version"],
    )
    def test_似ているが安全なコマンドは通す(self, command: str, tmp_path: Path) -> None:
        assert run_hook(self.HOOK, bash(command), tmp_path) == ALLOWED

    def test_Bash以外のツールは見ない(self, tmp_path: Path) -> None:
        payload = read_file("gh auth token")
        assert run_hook(self.HOOK, payload, tmp_path) == ALLOWED


class Test元に戻せない操作:
    HOOK = "block_destructive_commands.py"

    @pytest.mark.parametrize(
        "command",
        [
            "git reset --hard HEAD~3",
            "git clean -fd",
            "git push origin main --force",
            "git branch -D feature/x",
            "rm -rf /",
            "chmod 777 /etc/passwd",
            "DROP TABLE users;",
            "TRUNCATE TABLE sessions;",
        ],
    )
    def test_取り返しのつかない操作は止まる(self, command: str, tmp_path: Path) -> None:
        assert run_hook(self.HOOK, bash(command), tmp_path) == BLOCKED

    @pytest.mark.parametrize(
        "command",
        [
            "git push origin main --force-with-lease",
            "git reset --soft HEAD~1",
            "git branch -d feature/x",
            "rm -rf ./build",
            "git clean -n",
            "chmod 755 script.sh",
        ],
    )
    def test_安全な書き方は通す(self, command: str, tmp_path: Path) -> None:
        assert run_hook(self.HOOK, bash(command), tmp_path) == ALLOWED

    def test_環境変数で明示的に許可すると通す(self, tmp_path: Path) -> None:
        code = run_hook(
            self.HOOK,
            bash("git reset --hard"),
            tmp_path,
            {"GUARDRAILS_ALLOW_DESTRUCTIVE": "1"},
        )
        assert code == ALLOWED


class Test壊れた入力:
    """入力が壊れていることを理由に、利用者の操作を止めてはいけません。

    フックが落ちて終了コード1を返すと、ハーネスは警告を出して操作を通します。
    止まりはしませんが、利用者には毎回警告が出ます。邪魔に感じてフックを外す
    理由になるので、壊れた入力でも静かに通すことを守ります。
    """

    @pytest.mark.parametrize("hook", ALL_HOOKS)
    def test_空の入力でも通す(self, hook: str, tmp_path: Path) -> None:
        assert run_hook(hook, {}, tmp_path) == ALLOWED

    @pytest.mark.parametrize("hook", ALL_HOOKS)
    def test_想定外の型が入っていても通す(self, hook: str, tmp_path: Path) -> None:
        payload = {"tool_name": "Bash", "tool_input": {"command": 12345}}
        assert run_hook(hook, payload, tmp_path) == ALLOWED

    @pytest.mark.parametrize("hook", ALL_HOOKS)
    def test_JSONとして読めない入力でも通す(self, hook: str, tmp_path: Path) -> None:
        assert run_hook(hook, "これはJSONではありません", tmp_path) == ALLOWED

    @pytest.mark.parametrize("hook", ALL_HOOKS)
    def test_入力が空文字列でも通す(self, hook: str, tmp_path: Path) -> None:
        assert run_hook(hook, "", tmp_path) == ALLOWED


class Testログ:
    """ログに秘密が残らないことを守ります。

    フックが受け取るコマンド文字列には、ヘッダに載せたトークンのような秘密が
    混ざることがあります。判定の結果だけを書き、入力そのものは書きません。
    """

    def test_止めたときも入力の中身をログに書かない(self, tmp_path: Path) -> None:
        secret = "sk-test-これは秘密の値です"
        run_hook("block_secret_dump_commands.py", bash(f"gh auth token # {secret}"), tmp_path)

        written = "".join(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.log"))
        assert secret not in written
