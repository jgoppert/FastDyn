"""Conservative host-side candidate memory-access analysis.

The analysis deliberately starts from the correctness baseline: every ARM
Thumb instruction that Capstone exposes with a memory operand is a candidate.
Consumers can later add proof-based pruning, but must never drop an access
merely because its possible pointer flow is unknown.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import capstone
from capstone.arm import ARM_OP_MEM
from elftools.elf.elffile import ELFFile


@dataclass(frozen=True)
class CandidateAccess:
    pc: int


class ObjectAccessAnalysis:
    """Shared conservative access-plan producer for object-aware plugins."""

    def __init__(self, binary: Path, architecture: str):
        self.binary = binary
        self.architecture = architecture

    def candidate_accesses(self) -> tuple[CandidateAccess, ...]:
        if not self.architecture.lower().startswith("arm"):
            raise ValueError("ObjectAccessAnalysis currently supports ARM Thumb/M-profile binaries")
        candidates: set[int] = set()
        with self.binary.open("rb") as stream:
            elf = ELFFile(stream)
            decoder = capstone.Cs(capstone.CS_ARCH_ARM,
                                  capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
            decoder.detail = True
            for section in elf.iter_sections():
                if not section['sh_flags'] & 0x4 or not section['sh_size']:
                    continue
                for instruction in decoder.disasm(section.data(), int(section['sh_addr'])):
                    mnemonic = instruction.mnemonic.lower()
                    # Capstone represents normal LDR/STR addressing with a
                    # memory operand. Register-list transfers use no such
                    # operand despite performing a guest memory access.
                    if (any(operand.type == ARM_OP_MEM for operand in instruction.operands)
                            or mnemonic.startswith(("ldm", "stm", "push", "pop", "vld", "vst"))):
                        candidates.add(int(instruction.address) & ~1)
        return tuple(CandidateAccess(pc) for pc in sorted(candidates))
