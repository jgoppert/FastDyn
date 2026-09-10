# FastDyn with Rumoca

Run ArduPilot firmware in FastDyn's patched QEMU with a Modelica vehicle compiled
by Rumoca. The firmware uses FastDyn's emulated sensor and actuator drivers;
the FMI 3.0 plant advances with QEMU's virtual clock. See [FMI 3 and FastDyn](fmi.md)
for the FMU format, Model Exchange versus Co-Simulation, and the current
firmware-to-plant interface.

This book starts with a working Copter mission, explains how to choose and edit
models in TOML, and covers Plane and Rover runs. The resizing example uses the
original **5-inch Lumenier QAV-R, with a 220 mm motor diagonal**.

The environment pins **Rumoca 0.10.0** and the array-based modelica_models
library. The same compiler runs the first mission, QAV-R tuning, load
experiments, and payload study. ArduPilot firmware is **4.6.2**. Exact source
revisions are recorded in `flake.lock`, the Git submodules, and each result's
provenance file.

**Available now:** Copter, QAV-R, and Rover. **Plane is pending compiler
support** for the template's landing-gear contact events. Its Modelica source
and an earlier recorded mission are included for inspection; the current
three-wheel model is not presented as a runnable flight example.

Start with [environment setup](../general/environment.md), or read
[how to preview this book locally](../documentation.md).
