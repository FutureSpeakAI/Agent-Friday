# Regression Hunt — Automated Git Bisection

## Objective
Find the exact commit that introduced a specific regression. Uses binary
search through git history to narrow down the introducing commit, then
analyzes the diff to identify the root cause.

## Editable Surface
(none — this directive is read-only, it only checks out commits)

## Metric
Binary search progress — number of commits remaining to check. Lower is better.
`echo "remaining"`

## Loop
1. Identify the known-good commit (last passing) and known-bad commit (first failing)
2. Compute the midpoint commit: `git rev-list --count <good>..<bad>`
3. Check out the midpoint: `git checkout <midpoint>`
4. Run the failing test: `npx vitest run <test-file>`
5. If test passes: this commit is good — move the good boundary forward
6. If test fails: this commit is bad — move the bad boundary back
7. Repeat until good and bad are adjacent
8. Analyze the diff: `git diff <good> <bad>`
9. Report the introducing commit, the specific change, and recommended fix

## Constraints
- NEVER modify any files — only checkout and test
- Always return to the original branch when done
- Never force-push or reset the repository
- Record every bisection step for the user

## Budget
2 minutes per cycle, 15 cycles

## Circuit Breaker
- Cannot check out a commit (dirty working tree)
- Test command itself is broken (fails on known-good commits)
