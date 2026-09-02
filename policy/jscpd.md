# What counts as a duplicate

Three repos were running jscpd with three different answers, which means the
tool was reporting three different things under one name:

| | minLines | minTokens | threshold |
|---|---|---|---|
| loregarden | 15 | 100 | 1 |
| loremaker | 5 | 50 | 0 |
| blobert | 10 | 10 | 10 |

They are not variations on a setting. They are different claims about what
duplication *is*.

## The decision: 15 lines, 100 tokens, threshold 0

**`minTokens: 10` is not a duplicate detector.** Ten tokens is `if (x) return
null;` — matching in a dozen honest places in any codebase. A detector that
fires there does not find copy-paste, it finds the language. blobert compensated
with `threshold: 10`, tolerating 10% duplication overall, which is the shape of
a check that has been turned down until it stops complaining rather than turned
*to* something.

**Five lines is usually a pattern, not a copy.** A five-line block appearing
twice is more often two places doing a small thing the same way — which is what
consistency looks like — than one place pasted from another.

**Fifteen lines and a hundred tokens is a copy.** A block that size being
identical is almost never coincidence, and is almost always worth a shared
helper. That is the finding a duplication check exists to produce.

**Threshold 0, because at that sensitivity zero is reachable.** A tolerance
exists to absorb false positives; when there are few, a tolerance just permits
real duplication to accumulate under the bar. loregarden's `1` and blobert's `10`
are both allowances for detectors that were too sensitive.

## Adopting this on a repo that already has duplication

Do not turn the numbers down. Set `threshold` to the repo's current percentage,
commit that, and ratchet it toward 0 — the same "don't make it worse" policy the
organization gates use. A threshold that encodes today's debt is honest; a
`minTokens` that hides it is not.

## Tests are excluded

Deliberately. Test files repeat setup by design, and a duplication check that
fires on three tests arranging the same fixture teaches people to stop reading
its output.
