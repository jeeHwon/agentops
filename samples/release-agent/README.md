# Release Sample Agent

두 개의 UC Skill을 하나의 Agent Release로 고정하는 최소 예제입니다.

```bash
aops validate samples/release-agent
aops publish samples/release-agent --profile <profile>
aops assemble samples/release-agent --profile <profile>
cd samples/release-agent/.runtime && codex
```

`release.yaml`은 UC Skill 이름, 논리 버전과 SHA-256을 고정합니다. 이미 게시된 Skill은
수정하지 않습니다. Skill을 변경하면 새 UC Skill 이름과 버전, 새 해시, 새 Agent
`release_version`을 사용합니다.
