# Release Sample Agent

두 개의 UC Skill을 하나의 Agent Release로 고정하는 최소 예제입니다.

```bash
aops validate samples/release-agent
aops publish samples/release-agent
aops assemble samples/release-agent
cd samples/release-agent && codex
```

`agent.yaml`의 `release` 영역은 UC Skill 이름, 논리 버전과 SHA-256을 고정합니다. 이미 게시된 Skill은
수정하지 않습니다. Skill을 변경하면 새 UC Skill 이름과 버전, 새 해시, 새 Agent
Release 버전을 사용합니다.
