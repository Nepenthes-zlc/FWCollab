# C5 Information-dependent mini-suite specification

Status: pre-registered design; no model result was used to select a task.

## Purpose

The frozen 72-task benchmark primarily measures decentralized behavioral coordination under shared observation. This mini-suite isolates complementary private information. For every task:

\[
I_F \not\models solution,\qquad I_W \not\models solution,\qquad I_F \cup I_W \models solution.
\]

F observes its controller room and a private controller-to-actuator wiring permutation, but not W's route. W observes its route and the required actuator ID, but not the wiring or F's position. A one-round delayed public message is the only channel that joins the two facts.

## Fixed suite

- 12 tasks: four wiring permutations crossed with three required routes.
- Three controller choices and three actuator choices per task.
- Unit-step simultaneous actions, 30-round budget, one-round message delay.
- Frozen underlying world remains authoritative and replayable; only the DTO supplied to each policy is projected.
- Task IDs are replaced by `C5-HIDDEN` in model observations.
- Teammate coordinates and the other room are hidden, preventing implicit positional signaling.
- Correct-route travel fits the budget; serially exploring a wrong controller and then the correct one does not.

The generated manifest contains a finite-world certificate. Within every F observation-equivalence class, at least two controllers can be correct. Within every W observation-equivalence class, at least two controllers can be correct. Each joint pair identifies exactly one correct controller. Initial observation noninterference is checked by byte-equivalent canonical projections.

## Executable collaboration DAG

Each task uses the same typed dependency pattern with task-specific predicates:

`W knows required actuator -> W sends fact -> F receives fact -> F activates mapped controller -> required actuator opens -> W crosses -> team succeeds`

Message predicates are private evaluator inputs. The reference DAG, wiring, target and answer must never enter either model prompt.

## Minimal experiment matrix

Run only four conditions with three task-aligned seeds:

| Condition | Pair | Communication |
|---|---|---|
| GPT Comm | GPT-5.5 + GPT-5.5 | enabled |
| GPT NoComm | GPT-5.5 + GPT-5.5 | disabled |
| Gemini Comm | Gemini + Gemini | enabled |
| Gemini NoComm | Gemini + Gemini | disabled |

Total: `12 tasks x 4 conditions x 3 seeds = 144 episodes`.

Primary analysis is a task-level paired comparison of Comm versus NoComm for SR, DAG completion, progress AUC, clean information handoff, dependency violations and rounds-to-success. A nonzero NoComm success rate is expected from guessing; communication necessity is established by the information partition and quantified by the paired performance gap, not by requiring NoComm SR to equal zero.

## Acceptance gates before model execution

1. All 12 maps parse under the current v3 engine.
2. The finite-world information certificate passes.
3. Initial F projections are identical across routes within a wiring class.
4. Initial W projections are identical across wirings within a route class.
5. Deterministic solution witnesses finish within 30 rounds.
6. A wrong-first controller witness cannot finish within 30 rounds.
7. Trace output stores the actual per-role projected DTO for audit.

Phase-II files and evaluator semantics remain frozen and are not modified by this extension.
