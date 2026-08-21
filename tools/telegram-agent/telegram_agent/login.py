from __future__ import annotations

from pathlib import Path

from openai_codex import Codex, CodexConfig


def main() -> None:
    project_root = Path(__file__).resolve().parents[3]
    with Codex(CodexConfig(cwd=str(project_root))) as codex:
        if codex.account().account is not None:
            print("Codex is already authenticated for this Windows user.")
            return
        handle = codex.login_chatgpt_device_code()
        print("Open this URL in a browser:")
        print(handle.verification_url)
        print("Enter this one-time code:")
        print(handle.user_code)
        print("Waiting for authentication to finish...")
        handle.wait()
        if codex.account(refresh_token=True).account is None:
            raise SystemExit("Codex authentication did not complete.")
        print("Codex authentication completed.")


if __name__ == "__main__":
    main()
