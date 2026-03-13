"""
MODBUS TCP protocol helper.

Uses pymodbus to probe discovered MODBUS endpoints and read coil/register data.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("warclaw.modbus")


@dataclass
class ModbusDevice:
    host: str
    port: int
    unit_id: int
    reachable: bool
    coils: Optional[list[bool]] = None
    holding_registers: Optional[list[int]] = None
    error: Optional[str] = None


async def probe_modbus(host: str, port: int = 502, unit_id: int = 1,
                       num_coils: int = 16, num_registers: int = 16) -> ModbusDevice:
    """Async probe of a MODBUS TCP endpoint."""
    try:
        from pymodbus.client import AsyncModbusTcpClient  # type: ignore

        client = AsyncModbusTcpClient(host=host, port=port, timeout=3)
        connected = await client.connect()
        if not connected:
            return ModbusDevice(host=host, port=port, unit_id=unit_id,
                                reachable=False, error="Connection refused")

        coils_result = await client.read_coils(0, num_coils, slave=unit_id)
        coils = coils_result.bits[:num_coils] if not coils_result.isError() else None

        regs_result = await client.read_holding_registers(0, num_registers, slave=unit_id)
        regs = regs_result.registers if not regs_result.isError() else None

        await client.close()
        return ModbusDevice(host=host, port=port, unit_id=unit_id,
                            reachable=True, coils=list(coils) if coils else None,
                            holding_registers=list(regs) if regs else None)

    except Exception as e:
        log.debug("MODBUS probe %s:%d failed: %s", host, port, e)
        return ModbusDevice(host=host, port=port, unit_id=unit_id,
                            reachable=False, error=str(e))


def is_modbus_response(data: bytes) -> bool:
    """Heuristic: MODBUS TCP frames start with a 6-byte MBAP header."""
    if len(data) < 8:
        return False
    # Protocol ID bytes 2-3 must be 0x0000
    return data[2] == 0x00 and data[3] == 0x00
