# A one-hour walkthrough

Choose [Nix, Docker, or a manual installation](../general/environment.md) and
prepare the dependencies before the session. Keep a terminal at the repository
root and this book open beside it. Plan on roughly 15 minutes per block, with
room to adjust the pace for questions:

| Approx. time | Walkthrough | Checkpoint |
| --- | --- | --- |
| 0–15 min | [Workflow](workflow.md), [FMI 3 and FastDyn](fmi.md), and [first mission](getting-started.md) | Explain the firmware/plant boundary; observe the baseline mission |
| 15–30 min | [Read the Modelica model](modelica.md) and [resize to a QAV-R](qavr.md) | Identify the geometry, inertia, motor, and sensor changes |
| 30–45 min | [Gain tuning](tuning.md) and [load at a motor](payload.md) | Compare measured gains; edit the force equation, rebuild, and run |
| 45–60 min | [Payload study](monte-carlo.md), [mission reports](mission-reports.md), and [roadmap discussion](library-roadmap.md) | Compare successful and failed runs; choose a simulation approach |

Run lengthy tuning and Monte Carlo batches ahead of time. The checked-in
mission reports and all 18 payload-study trajectories can be explored
immediately, leaving time to inspect source, change TOML settings, and discuss
results during the session.

The first mission, gain comparison, and force-equation exercise use the current
array-based models and the pinned Rumoca compiler. These builds and flights
have passed locally. Plane remains a source/recorded-result example until the
compiler supports its contact events.

The hands-on target is to choose a source model, explain one change to its
physics, rebuild the selected FMU, and compare a fresh run with a baseline.
Use the [varying-force exercise](payload.md#5-change-an-equation-rebuild-and-fly)
for that end-to-end task. Discuss the recorded tuning comparison and full Monte
Carlo batch instead of rerunning both during the hour. If discussion runs long,
finish the equation edit as follow-up work using the complete reference model.

Plane and Rover provide additional examples in [running models](running-models.md).
The walkthrough's gains and results describe this simulation; they are not
flight-tested hardware settings.
