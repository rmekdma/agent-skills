# Agent Skills for Codex

이 repo는 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)를 Codex 용도로 변환한 결과물입니다.

- 사용한 source commit: [`54c5adf`](https://github.com/addyosmani/agent-skills/commit/54c5adfc6b3b494b834d7c61a8feb41c9b5db083)

## 설치

```bash
install_root="$HOME/.agents/skills/agent-skills"
agents_root="$HOME/.codex/agents"
mkdir -p "$install_root" "$agents_root"

rm -rf "$install_root/references" "$install_root/skills" "$install_root/.codex-plugin"
cp -R references skills .codex-plugin "$install_root"/
cp agents/*.toml "$agents_root"/
```

## Update

원본 clone이 업데이트되면 source clone root에서 다음 명령으로 이 Codex용 출력물을 다시 생성합니다.

```bash
./agent-skills/update-from-source.py
```
