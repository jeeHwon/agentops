# Release Sample Agent

두 개의 UC Skill을 하나의 Agent Release로 고정하는 최소 예제입니다.

```bash
aops validate samples/release-agent
aops assemble samples/release-agent
aops register samples/release-agent --version 3.1.0
cd samples/release-agent && codex
```

이 Release는 기존 UC Skill `3.0.0` 두 개를 명시적으로 고정합니다. Harness만 변경했으므로
Agent Release를 `3.1.0`으로 올리고 Skill 버전은 그대로 유지합니다.
