"""The STM32F429I-DISC1 LED fixture remains a real FreeRTOS ELF."""

from pathlib import Path
import tomllib

from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = (
    ROOT
    / "tests"
    / "binaries"
    / "freertos_stm32f429i_discovery"
    / "freertos_stm32f429i_discovery.elf"
)


def test_freertos_stm32f429i_discovery_fixture_exports_scheduler_and_led_task_state():
    with FIXTURE.open("rb") as stream:
        elf = ELFFile(stream)
        symbols = {
            symbol.name
            for section in elf.iter_sections()
            if isinstance(section, SymbolTableSection)
            for symbol in section.iter_symbols()
        }

        assert elf.header["e_machine"] == "EM_ARM"
        assert elf.has_dwarf_info()
        assert elf.get_section_by_name(".isr_vector")["sh_addr"] == 0x08000000

    assert {"vTaskSwitchContext", "xPortPendSVHandler", "xPortSysTickHandler"} <= symbols
    assert {
        "freertos_led_green_toggles",
        "freertos_led_red_toggles",
    } <= symbols


def test_freertos_stm32f429i_discovery_config_targets_the_real_board_and_classic_mmio():
    config_path = ROOT / "configs" / "freertos_stm32f429i_discovery.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    cpu = config["CPU"]["cpu0"][0]
    assert config["Machine"]["platform"] == "STM32F429"
    assert cpu["cpu"] == "cortex-m4"
    assert cpu["binary"] == str(FIXTURE.relative_to(ROOT))
    assert config["Device"]["unmapped_peripherals"]["handlers"][0]["model"] == "classic"
