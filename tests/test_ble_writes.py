import asyncio

import cbor2

from omapuffco.ble import PuffcoBLE
from omapuffco.codec import hexify
from omapuffco.lights import solid_color_payload

PATH = "/u/app/hc/3/colr"


def record_writes(payload, max_message=125):
    ble = PuffcoBLE()
    ble._max_message = max_message
    writes = []

    async def fake_write_short(path, offset, flags, value):
        writes.append((offset, bytes(value), 3 + 3 + len(path) + 1 + len(value)))

    ble.write_short = fake_write_short
    asyncio.run(ble.write_cbor_full(PATH, payload))
    return writes


def test_every_write_fits_one_ble_packet():
    for payload in ({"lamp": {"name": "solid", "param": {"color": ["#3dd68c"]}}}, solid_color_payload("#3dd68c")):
        assert all(size <= 125 for _, _, size in record_writes(payload))


def test_chunks_reassemble_to_the_exact_payload():
    payload = solid_color_payload("#3dd68c")
    blob = cbor2.dumps(hexify(payload), canonical=True)
    writes = record_writes(payload)
    data = writes[:-1]  # the last write clears the old tail
    assert len(data) > 1
    rebuilt = bytearray()
    for offset, value, _ in data:
        assert offset == len(rebuilt)
        rebuilt += value
    assert bytes(rebuilt) == blob
    tail_offset, tail, _ = writes[-1]
    assert tail_offset == len(blob) and set(tail) == {0}


def test_small_payload_is_a_single_write_plus_tail_clear():
    writes = record_writes({"lamp": {"name": "solid", "param": {"color": ["#3dd68c"]}}})
    assert len(writes) == 2
    assert writes[0][0] == 0
