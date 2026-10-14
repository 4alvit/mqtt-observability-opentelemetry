# Governance and maintenance

This project maintains MQTT tracing, broker metrics and the accompanying observability and recovery components. It is maintained by the `4alvit` account. The primary maintainer owns project scope, review decisions, dependency updates, vulnerability response and release acceptance. Contributors propose issues, tests, documentation and pull requests. Automated checks and review bots provide evidence; they do not replace maintainer responsibility or authorize a deployment.

Use public GitHub issues for design proposals and ordinary problems, and pull requests for reviewed changes. Decisions should record the problem, compatibility effects and supporting tests. Contributors may disagree respectfully and request reconsideration with new evidence. Changes follow the repository's current protected-branch review and CI rules; no exception is implied by this document.

The [Code of Conduct](CODE_OF_CONDUCT.md) applies to project participation. Security reports use [SECURITY.md](SECURITY.md). Release acceptance and distribution follow [RELEASING.md](RELEASING.md); a passing test job does not establish physical deployment acceptance.

## Continuity limitations

There is currently one primary maintainer and no designated backup person or tested mechanism that restores repository, CI and release access within one week if that maintainer becomes unavailable. Public source and bot access do not supply that guarantee. We therefore do not claim OpenSSF Silver access continuity or a bus factor of two. Establishing and testing a recovery arrangement requires an actual authorized person or mechanism, not just a document.

No foundation membership, independent security audit or third-party certification is implied. The public OpenSSF record describes the evidence and remaining gaps of this individual project.
