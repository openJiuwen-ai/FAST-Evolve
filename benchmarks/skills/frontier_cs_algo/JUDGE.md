# Frontier-CS judge prerequisite

The AgentEvolve evaluator is a normal host-side Python process. It does not
need to run in a container and it never invokes Docker.

Frontier-CS algorithmic scoring still requires an official-compatible judge
service because the public CORAL task packs contain statements and seed
programs, not the hidden tests, checkers, interactors, compiler sandbox, or
scoring runtime. By default the evaluator connects to:

```text
http://localhost:8081
```

To use an existing service at another address, set:

```powershell
$env:FRONTIER_CS_JUDGE_URL = "http://judge-host:8081"
```

The service must expose the Frontier-CS v1 endpoints `POST /submit` and
`GET /result/{submission_id}` and contain the same problem IDs listed in
`suite_manifest.json`. The pinned reference implementation is:

```text
repository: https://github.com/FrontierCS/Frontier-CS.git
tag: v1.0.0
revision: c7ec558160646dbe797828ccd1a4fded503ab87c
path: algorithmic/
```

That judge deployment may use Docker/go-judge; it remains outside this
repository and outside the AgentEvolve evaluator process.
