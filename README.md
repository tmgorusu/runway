# planner
# Runway
Wear leveling for utility-dispatched home batteries. A utility calls the fleet for a megawatt target. Runway chooses which homes deliver it so a hot, contract-worn battery does not take the same energy as a cool one. Backup reserve stays untouched. A call the feasible fleet cannot cover is reported as a shortfall.
The fleet in this plan is synthetic. The ERCOT sample is real public data. There is no Base telemetry here.
## Plan
| File | What it is |
| --- | --- |
| [BUILD_PLAN.md](BUILD_PLAN.md) | Full council plan: decisions, milestones, task cards, cut list, video outline. |
| [tasks.json](tasks.json) | The same task cards as JSON. |
## Specs
| Spec | Who it is for |
| --- | --- |
| [specs/VISION.md](specs/VISION.md) | The merged system. What the demo, the files, and the five-minute video look like when both halves land. |
| [specs/WEAR.md](specs/WEAR.md) | The battery researcher. Heat, aging, the wear scalar, the baselines, the hero ratio, the replay. |
| [specs/MACHINE.md](specs/MACHINE.md) | The other hacker. Cache, calendar, water-fill, workers, dispatcher, benchmark, dashboard, demo. |
Wear and Machine can each hand their spec to a separate model council. The vision spec is the contract those councils are not free to reopen.